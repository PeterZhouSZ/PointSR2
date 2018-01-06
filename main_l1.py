import argparse
import os
import socket
import sys
import time
from glob import glob

import numpy as np
import tensorflow as tf
from matplotlib import pyplot as plt
from tqdm import tqdm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
import data_provider
import model_utils
import generator1_upsample2 as MODEL_GEN
from data_provider import NUM_EDGE, NUM_FACE
from GKNN import GKNN
from tf_ops.sampling.tf_sampling import farthest_point_sample
from utils import pc_util

parser = argparse.ArgumentParser()
parser.add_argument('--phase', default='test', help='train or test [default: train]')
parser.add_argument('--gpu', default='0', help='GPU to use [default: GPU 0]')
parser.add_argument('--log_dir', default='../model/NEWVirtualscan_generator1_1k_sharpmix_crop_l1_2', help='Log dir [default: log]')
parser.add_argument('--num_point', type=int, default=1024,help='Point Number [1024/2048] [default: 1024]')
parser.add_argument('--num_addpoint', type=int, default=80,help='Add Point Number [default: 600]')# train(1k) is 256, test is 48(60)?
parser.add_argument('--up_ratio',  type=int,  default=4,   help='Upsampling Ratio [default: 2]')
parser.add_argument('--is_crop',type= bool, default=True, help='Use cropped points in training [default: False]')
parser.add_argument('--max_epoch', type=int, default=200, help='Epoch to run [default: 500]')  #(nocrop:180 crop:200)
parser.add_argument('--batch_size', type=int, default=16, help='Batch Size during training [default: 32]') #(512:16 1k:8,is_crop:16)
parser.add_argument('--learning_rate', type=float, default=0.001)
parser.add_argument('--assign_model_path',default=None, help='Pre-trained model path [default: None]')
parser.add_argument('--use_uniformloss',type= bool, default=False, help='Use uniformloss [default: False]')

FLAGS = parser.parse_args()
print socket.gethostname()
print FLAGS

ASSIGN_MODEL_PATH=FLAGS.assign_model_path
USE_UNIFORM_LOSS = FLAGS.use_uniformloss
IS_CROP = FLAGS.is_crop
PHASE = FLAGS.phase
GPU_INDEX = FLAGS.gpu
BATCH_SIZE = FLAGS.batch_size
NUM_POINT = FLAGS.num_point
NUM_ADDPOINT = FLAGS.num_addpoint
UP_RATIO = FLAGS.up_ratio
MAX_EPOCH = FLAGS.max_epoch
BASE_LEARNING_RATE = FLAGS.learning_rate
MODEL_DIR = FLAGS.log_dir
os.environ['CUDA_VISIBLE_DEVICES'] = GPU_INDEX

def log_string(out_str):
    global LOG_FOUT
    LOG_FOUT.write(out_str)
    LOG_FOUT.flush()

class Network(object):
    def __init__(self):
        return

    def build_graph(self,is_training=True,scope='generator'):
        bn_decay = 0.95
        self.step = tf.Variable(0, trainable=False)
        self.pointclouds_input = tf.placeholder(tf.float32, shape=(BATCH_SIZE, NUM_POINT, 3))
        self.pointclouds_radius = tf.placeholder(tf.float32, shape=(BATCH_SIZE))
        self.pointclouds_poisson = tf.placeholder(tf.float32, shape=(BATCH_SIZE, NUM_POINT, 3))
        # self.pointclouds_dist = tf.placeholder(tf.float32, shape=(BATCH_SIZE, NUM_POINT))
        self.pointclouds_idx = tf.placeholder(tf.int32,shape=(BATCH_SIZE,NUM_POINT,2))
        self.pointclouds_edge = tf.placeholder(tf.float32, shape=(BATCH_SIZE, NUM_EDGE, 6))
        self.pointclouds_plane = tf.placeholder(tf.float32, shape=(BATCH_SIZE, NUM_FACE, 9))
        self.pointclouds_plane_normal = tf.placeholder(tf.float32, shape=(BATCH_SIZE, NUM_FACE,3))

        # create the generator model
        self.pred_dist, self.pred_coord,self.idx,_ = MODEL_GEN.get_gen_model(self.pointclouds_input, is_training, scope=scope, bradius=1.0,
                                                    num_addpoint=NUM_ADDPOINT,reuse=None, use_normal=False, use_bn=False,use_ibn=False,
                                                    bn_decay=bn_decay, up_ratio=UP_RATIO,idx=self.pointclouds_idx,is_crop=IS_CROP)

        ###calculate the distance ground truth of upsample_point
        self.pointclouds_dist = model_utils.distance_point2edge(self.pred_coord,self.pointclouds_edge)
        self.pointclouds_dist = tf.sqrt(tf.reduce_min(self.pointclouds_dist,axis=-1))
        self.pointclouds_dist_truncated = tf.minimum(0.5,self.pointclouds_dist)
        self.pred_dist = tf.minimum(0.5,tf.maximum(0.0,self.pred_dist))

        # gather the edge
        self.pred_edgecoord = tf.gather_nd(self.pred_coord, self.idx)
        self.pred_edgedist = tf.gather_nd(self.pred_dist, self.idx)
        self.edgedist = tf.gather_nd(self.pointclouds_dist_truncated,self.idx)

        # ## The following code is okay when the batch size is 1
        self.edge_threshold = tf.constant(0.05,tf.float32,[1]) # select a small value when use 1k points
        if True:
            indics = tf.where(tf.less_equal(self.pred_edgedist,self.edge_threshold)) #(?,2)
            self.select_pred_edgecoord = tf.gather_nd(self.pred_edgecoord, indics) #(?,3)
            self.select_pred_edgedist  = tf.gather_nd(self.pred_edgedist, indics) #(?,3)
        else:
            indics = tf.where(tf.less_equal(self.pointclouds_dist_truncated, self.edge_threshold))  # (?,2)
            self.select_pred_edgecoord = tf.gather_nd(self.pred_coord, indics)  # (?,3)
            self.select_pred_edgedist = tf.gather_nd(self.pred_dist, indics)  # (?,3)

        # if is_training is False:
        #     input_dist = model_utils.distance_point2edge(self.pointclouds_input,self.pointclouds_edge)
        #     input_dist = tf.sqrt(tf.reduce_min(input_dist,axis=-1))
        #     indics = tf.where(tf.less_equal(input_dist,self.edge_threshold))
        #     self.select_input_edge = tf.gather_nd(self.pointclouds_input, indics)
        #     self.select_input_edgedist  = tf.gather_nd(input_dist,indics)

        if is_training is False:
            return

        self.dist_mseloss = 1.0/(0.4+self.pointclouds_dist_truncated)*(self.pointclouds_dist_truncated - self.pred_dist) ** 2
        self.dist_mseloss = tf.reduce_mean(self.dist_mseloss / tf.expand_dims(self.pointclouds_radius ** 2, axis=-1))
        tf.summary.scalar('loss/dist_loss', self.dist_mseloss)
        tf.summary.histogram('dist/gt', self.pointclouds_dist_truncated)
        tf.summary.histogram('dist/pred', self.pred_dist)

        # weight = tf.pow(0.98, tf.to_float(tf.div(self.step,200)))
        weight = tf.maximum(0.5-tf.to_float(self.step)/20000.0,0.0)
        self.edgemask = tf.to_float(tf.less_equal(weight * self.edgedist + (1 - weight) * self.pred_edgedist, 0.15))
        self.edge_loss = tf.reduce_sum(self.edgemask * self.edgedist/ tf.expand_dims(self.pointclouds_radius, axis=-1)) / (tf.reduce_sum(self.edgemask) + 1.0)
        tf.summary.scalar('weight',weight)
        tf.summary.histogram('dist/edge_dist', self.edgedist)
        tf.summary.histogram('loss/edge_mask', self.edgemask)
        tf.summary.scalar('loss/edge_loss', self.edge_loss)

        with tf.device('/gpu:0'):
            self.plane_dist = model_utils.distance_point2mesh(self.pred_coord, self.pointclouds_plane)
            idx = tf.argmin(self.plane_dist, axis=2,output_type=tf.int32)
            idx0 = tf.tile(tf.reshape(tf.range(BATCH_SIZE), (BATCH_SIZE, 1)), (1, NUM_POINT*UP_RATIO/2))
            face_normal = tf.gather_nd(self.pointclouds_plane_normal,tf.stack([idx0,idx],axis=-1))

            # self.plane_dist = tf.where(tf.is_nan(self.plane_dist),tf.zeros_like(self.plane_dist),self.plane_dist)
            self.plane_dist = tf.reduce_min(self.plane_dist, axis=2)
            self.plane_dist = tf.sqrt(self.plane_dist+1e-12)
            tf.summary.histogram('dist/plane_dist',self.plane_dist)

            self.plane_loss = 5*tf.reduce_mean(self.plane_dist / tf.expand_dims(self.pointclouds_radius, axis=-1))
            tf.summary.scalar('loss/plane_loss', self.plane_loss)

        self.perulsionloss = model_utils.get_perulsion_loss1_orthdistance(self.pred_coord,face_normal, numpoint=NUM_POINT*UP_RATIO,use_l1=True)
        # self.perulsionloss = model_utils.get_perulsion_loss1(self.pred_coord, numpoint=NUM_POINT * UP_RATIO, use_l1=True)
        tf.summary.scalar('loss/perulsion_loss', self.perulsionloss)

        self.total_loss = 10*(self.dist_mseloss + self.plane_loss + self.edge_loss + self.perulsionloss) + tf.losses.get_regularization_loss()


        gen_update_ops = [op for op in tf.get_collection(tf.GraphKeys.UPDATE_OPS) if op.name.startswith(scope)]
        gen_tvars = [var for var in tf.trainable_variables() if var.name.startswith(scope)]
        with tf.control_dependencies(gen_update_ops):
            self.pre_gen_train = tf.train.AdamOptimizer(BASE_LEARNING_RATE, beta1=0.9).minimize(self.total_loss, var_list=gen_tvars,
                                                                                                colocate_gradients_with_ops=False,
                                                                                                global_step=self.step)
        # merge summary and add pointclouds summary
        tf.summary.scalar('loss/regularation', tf.losses.get_regularization_loss())
        tf.summary.scalar('loss/total_loss', self.total_loss)
        self.merged = tf.summary.merge_all()

        self.pointclouds_image_input = tf.placeholder(tf.float32, shape=[None, 500, 1500, 1])
        pointclouds_input_summary = tf.summary.image('1_input', self.pointclouds_image_input, max_outputs=1)
        self.pointclouds_image_pred = tf.placeholder(tf.float32, shape=[None, 500, 1500, 1])
        pointclouds_pred_summary = tf.summary.image('2_pred', self.pointclouds_image_pred, max_outputs=1)
        self.pointclouds_image_gt = tf.placeholder(tf.float32, shape=[None, 500, 1500, 1])
        pointclouds_gt_summary = tf.summary.image('3_edge', self.pointclouds_image_gt, max_outputs=1)
        self.image_merged = tf.summary.merge([pointclouds_input_summary, pointclouds_pred_summary, pointclouds_gt_summary])

    def train(self,assign_model_path=None):
        # Create a session
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        config.allow_soft_placement = True
        # config.log_device_placement = False
        with tf.Session(config=config) as self.sess:
            self.train_writer = tf.summary.FileWriter(os.path.join(MODEL_DIR, 'train'), self.sess.graph)
            init = tf.global_variables_initializer()
            self.sess.run(init)

            # restore the model
            saver = tf.train.Saver(max_to_keep=6)
            restore_epoch, checkpoint_path = model_utils.pre_load_checkpoint(MODEL_DIR)
            global LOG_FOUT
            if restore_epoch == 0:
                LOG_FOUT = open(os.path.join(MODEL_DIR, 'log_train.txt'), 'w')
                LOG_FOUT.write(str(socket.gethostname()) + '\n')
                LOG_FOUT.write(str(FLAGS) + '\n')
            else:
                LOG_FOUT = open(os.path.join(MODEL_DIR, 'log_train.txt'), 'a')
                saver.restore(self.sess, checkpoint_path)

            ###assign the generator with another model file
            if assign_model_path is not None:
                print "Load pre-train model from %s" % (assign_model_path)
                assign_saver = tf.train.Saver(
                    var_list=[var for var in tf.trainable_variables() if var.name.startswith("generator")])
                assign_saver.restore(self.sess, assign_model_path)

            ##read data
            self.fetchworker = data_provider.Fetcher(BATCH_SIZE, NUM_POINT)
            self.fetchworker.start()
            for epoch in tqdm(range(restore_epoch, MAX_EPOCH + 1), ncols=55):
                log_string('**** EPOCH %03d ****\t' % (epoch))
                self.train_one_epoch()
                if epoch % 20 == 0:
                    saver.save(self.sess, os.path.join(MODEL_DIR, "model"), global_step=epoch)
            self.fetchworker.shutdown()

    def train_one_epoch(self):
        loss_sum = []
        fetch_time = 0
        for batch_idx in range(self.fetchworker.num_batches):
            start = time.time()
            batch_data_input, batch_data_clean, batch_data_dist, batch_data_edgeface, radius,point_order = self.fetchworker.fetch()
            batch_data_edge = np.reshape(batch_data_edgeface[:,0:2*NUM_EDGE,:],(BATCH_SIZE,NUM_EDGE,6))
            batch_data_face = np.reshape(batch_data_edgeface[:, 2*NUM_EDGE:2*NUM_EDGE+3*NUM_FACE,:],(BATCH_SIZE, NUM_FACE, 9))
            A = batch_data_face[:,:,3:6]-batch_data_face[:,:,0:3]
            B = batch_data_face[:,:,6:9]-batch_data_face[:,:,0:3]
            batch_data_normal = np.cross(A,B)+1e-12
            batch_data_normal = batch_data_normal / np.sqrt(np.sum(batch_data_normal ** 2, axis=-1, keepdims=True))
            batch_data_edgepoint =batch_data_edgeface[:, 2*NUM_EDGE+3*NUM_FACE:, :]
            end = time.time()
            fetch_time += end - start

            feed_dict = {self.pointclouds_input: batch_data_input,
                         self.pointclouds_poisson: batch_data_clean,
                         # self.pointclouds_dist: batch_data_dist,
                         self.pointclouds_idx: point_order,
                         self.pointclouds_edge: batch_data_edge,
                         self.pointclouds_plane: batch_data_face,
                         self.pointclouds_plane_normal:batch_data_normal,
                         self.pointclouds_radius: radius}

            _, summary, step, pred_coord, pred_edgecoord, edgemask, edge_loss = self.sess.run(
                [self.pre_gen_train, self.merged, self.step, self.pred_coord, self.pred_edgecoord, self.edgemask, self.edge_loss], feed_dict=feed_dict)
            self.train_writer.add_summary(summary, step)
            loss_sum.append(edge_loss)
            edgemask[:,0:5]=1
            pred_edgecoord = pred_edgecoord[0][edgemask[0]==1]
            if step % 30 == 0:
                pointclouds_image_input = pc_util.point_cloud_three_views(batch_data_input[0, :, 0:3])
                pointclouds_image_input = np.expand_dims(np.expand_dims(pointclouds_image_input, axis=-1), axis=0)
                pointclouds_image_pred = pc_util.point_cloud_three_views(pred_coord[0, :, 0:3])
                pointclouds_image_pred = np.expand_dims(np.expand_dims(pointclouds_image_pred, axis=-1), axis=0)
                pointclouds_image_gt = pc_util.point_cloud_three_views(pred_edgecoord[:, 0:3])
                pointclouds_image_gt = np.expand_dims(np.expand_dims(pointclouds_image_gt, axis=-1), axis=0)
                feed_dict = {self.pointclouds_image_input: pointclouds_image_input,
                             self.pointclouds_image_pred: pointclouds_image_pred,
                             self.pointclouds_image_gt: pointclouds_image_gt}

                summary = self.sess.run(self.image_merged, feed_dict)
                self.train_writer.add_summary(summary, step)
        loss_sum = np.asarray(loss_sum)
        log_string('step: %d edge_loss: %f\n' % (step, round(loss_sum.mean(), 4)))
        print 'read data time: %s edge_loss: %f' % (round(fetch_time, 4), round(loss_sum.mean(), 4))

    def patch_prediction(self, patch_point,sess):
        #normalize the point clouds
        patch_point, centroid, furthest_distance = data_provider.normalize_point_cloud(patch_point)
        new_idx = np.stack((np.zeros((NUM_POINT)).astype(np.int64), np.arange(NUM_POINT)), axis=-1)
        pred, pred_edge, pred_edgedist = sess.run([self.pred_coord, self.select_pred_edgecoord, self.select_pred_edgedist],
                                                    feed_dict={self.pointclouds_input: np.expand_dims(patch_point,axis=0),
                                                               self.pointclouds_radius: np.ones(1),
                                                               self.pointclouds_idx:np.expand_dims(new_idx,axis=0)
                                                               # self.edge_threshold: 0.015 / furthest_distance,
                                                               })
        pred = np.squeeze(centroid + pred * furthest_distance, axis=0)
        pred_edge = centroid + pred_edge * furthest_distance
        pred_edgedist = pred_edgedist * furthest_distance
        return pred, pred_edge, pred_edgedist

    def patch_prediction_avg(self, patch_point, sess):
        #normalize the point clouds
        patch_point, centroid, furthest_distance = data_provider.normalize_point_cloud(patch_point)
        # print furthest_distance*0.075
        pred_list = []
        pred_edgecoord_list=[]
        pred_edgedist_list = []
        for iter in xrange(5):
            idx,new_idx = data_provider.get_inverse_index(patch_point.shape[0])
            new_idx = np.stack((np.zeros((NUM_POINT)).astype(np.int64), new_idx), axis=-1)
            patch_point_input = patch_point[idx].copy()
            pred, pred_edgecoord, pred_edgedist = sess.run([self.pred_coord, self.pred_coord, self.pred_dist],
                                                        feed_dict={self.pointclouds_input: np.expand_dims(patch_point_input,axis=0),
                                                                   self.pointclouds_radius: np.ones(1),
                                                                   self.pointclouds_idx: np.expand_dims(new_idx,axis=0)
                                                                   })
            pred_list.append(pred)
            pred_edgecoord_list.append(pred_edgecoord)
            pred_edgedist_list.append(pred_edgedist)

        pred = np.asarray(pred_list).mean(axis=0)
        pred_edgecoord = np.asarray(pred_edgecoord_list).mean(axis=0)
        pred_edgedist = np.asarray(pred_edgedist_list).mean(axis=0)

        idx = np.argsort(pred_edgedist,axis=-1)
        pred_edgedist = pred_edgedist[0][idx[0,:60]]
        pred_edgecoord = pred_edgecoord[0][idx[0,:60]]
        pred_edgecoord = pred_edgecoord[pred_edgedist<0.05] #0.015 / furthest_distance
        pred_edgedist = pred_edgedist[pred_edgedist<0.05]

        pred = np.squeeze(centroid + pred * furthest_distance, axis=0)
        pred_edgecoord = centroid + pred_edgecoord * furthest_distance
        pred_edgedist = pred_edgedist * furthest_distance
        return pred, pred_edgecoord, pred_edgedist

    def test_hierarical_prediction(self):
        data_folder = '../../PointSR_data/CAD_imperfect/new_simu_noise'
        #data_folder = '../../PointSR_data/virtualscan/new_simu_noise'
        data_folder = '../../PointSR_data/virtualscan/chair_test1'
        phase = data_folder.split('/')[-2]+"_"+data_folder.split('/')[-1]
        save_path = os.path.join(MODEL_DIR, 'result/' + phase)
        self.saver = tf.train.Saver()
        _, restore_model_path = model_utils.pre_load_checkpoint(MODEL_DIR)
        print restore_model_path

        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        config.allow_soft_placement = True
        with tf.Session(config=config) as sess:
            self.saver.restore(sess, restore_model_path)
            total_time = 0
            samples = glob(data_folder + "/*_noise_half.xyz")
            samples.sort(reverse=True)
            for point_path in samples:
                if 'no_noise' in point_path:
                    continue
                edge_path = point_path.replace('new_simu_noise', 'mesh_edge').replace('_noise_double.xyz', '_edge.xyz')
                edge_path = None
                print point_path, edge_path

                gm = GKNN(point_path, edge_path, patch_size=NUM_POINT, patch_num=30,add_noise=False,normalization=False)
                ## get patch seed from farthestsampling
                points = tf.convert_to_tensor(np.expand_dims(gm.data,axis=0),dtype=tf.float32)
                start= time.time()
                seed1_num = int(gm.data.shape[0] / (NUM_POINT / 8) * 5)
                seed = farthest_point_sample(seed1_num*2, points).eval()[0]
                print "farthest distance sampling cost", time.time() - start
                seed_list = seed[:seed1_num]

                up_point_list=[]
                up_edge_list = []
                up_edgedist_list = []
                fail = 0
                for seed in tqdm(seed_list):
                    try:
                        patch_size = int(NUM_POINT * np.random.uniform(1.0,2.0))
                        point = gm.data[gm.bfs_knn(seed, patch_size)]
                        idx = np.random.permutation(patch_size)[:NUM_POINT]
                        idx.sort()
                        point = point[idx]
                    except:
                        fail= fail+1
                        continue
                    up_point,up_edgepoint,up_edgedist = self.patch_prediction(point, sess)
                    up_point_list.append(up_point)
                    up_edge_list.append(up_edgepoint)
                    up_edgedist_list.append(up_edgedist)
                print "total %d fails" % fail

                path = os.path.join(save_path, point_path.split('/')[-1][:-4] + "_input.xyz")
                data_provider.save_xyz(path, gm.data)

                pred = np.concatenate(up_point_list,axis=0)
                path = os.path.join(save_path, point_path.split('/')[-1][:-4] + "_output.xyz")
                data_provider.save_xyz(path, pred)

                pred_edge = np.concatenate(up_edge_list, axis=0)
                print "total %d edgepoint" % pred_edge.shape[0]
                pred_edgedist = np.concatenate(up_edgedist_list,axis=0)
                rgba = data_provider.convert_dist2rgba(pred_edgedist, scale=10)
                path = os.path.join(save_path, point_path.split('/')[-1][:-4] + "_outputedge.ply")
                data_provider.save_ply(path, np.hstack((pred_edge, rgba, pred_edgedist.reshape(-1, 1))))

                if edge_path is not None:
                    t1 = time.time()
                    edge_dist = np.zeros(pred_edge.shape[0])
                    for sid in range(0,pred_edge.shape[0],20000):
                        eid = np.minimum(pred_edge.shape[0],sid+20000)
                        tf_point = tf.placeholder(tf.float32,[1,eid-sid,3])
                        tf_edge = tf.placeholder(tf.float32,[1,gm.edge.shape[0],6])
                        pred_edge_dist_tf = model_utils.distance_point2edge(tf_point,tf_edge)
                        pred_edge_dist_tf = tf.sqrt(tf.reduce_min(pred_edge_dist_tf, axis=-1))
                        edge_dist[sid:eid] = sess.run(pred_edge_dist_tf,feed_dict={tf_point:np.expand_dims(pred_edge[sid:eid], axis=0),
                                                                                   tf_edge:np.expand_dims(gm.edge, axis=0)})
                    t2 = time.time()
                    print "tf time %f"%(t2-t1)
                    rgba = data_provider.convert_dist2rgba(edge_dist, scale=10)
                    path = os.path.join(save_path, point_path.split('/')[-1][:-4] + "_outputedgeerror.ply")
                    data_provider.save_ply(path, np.hstack((pred_edge, rgba, edge_dist.reshape(-1, 1))))
            print total_time/len(samples)


    def test(self,show=False,use_normal=False):
        data_folder = '../../PointSR_data/CAD/mesh_MC16k'
        phase = data_folder.split('/')[-2]+data_folder.split('/')[-1]
        save_path = os.path.join(MODEL_DIR, 'result/' + phase)
        self.saver = tf.train.Saver()
        _, restore_model_path = model_utils.pre_load_checkpoint(MODEL_DIR)
        print restore_model_path

        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        config.allow_soft_placement = True
        with tf.Session(config=config) as sess:
            self.saver.restore(sess, restore_model_path)
            samples = glob(data_folder+"/.xyz")
            samples.sort()
            total_time = 0

            #input, dist, edge, data_radius, name = data_provider.load_patch_data(NUM_POINT, True, 30)
            #edge = np.reshape(edge,[-1,NUM_EDGE,6])

            for i,item in tqdm(enumerate(samples)):
                input = np.loadtxt(item)
                edge = np.loadtxt(item.replace('mesh_MC16k','mesh_edge').replace('.xyz','_edge.xyz'))
                idx = np.all(edge[:, 0:3] == edge[:, 3:6], axis=-1)
                edge = edge[idx == False]
                l = len(edge)
                idx = range(l) * (1300 / l) + list(np.random.permutation(l)[:1300 % l])
                edge = edge[idx]

                # # coord = input[:, 0:3]
                # # centroid = np.mean(coord, axis=0, keepdims=True)
                # # coord = coord - centroid
                # # furthest_distance = np.amax(np.sqrt(np.sum(abs(coord) ** 2, axis=-1)))
                # # coord = coord / furthest_distance
                # # input[:, 0:3] = coord
                input = np.expand_dims(input,axis=0)
                # input = data_provider.jitter_perturbation_point_cloud(input, sigma=0.01, clip=0.02)

                start_time = time.time()
                edge_pl = tf.placeholder(tf.float32, [1, edge.shape[0], 6])
                dist_gt_pl = tf.sqrt(tf.reduce_min(model_utils.distance_point2edge(self.pred, edge_pl), axis=-1))

                pred, pred_dist,dist_gt = sess.run([self.pred,self.pred_dist,dist_gt_pl],
                                                     feed_dict={self.pointclouds_input: input[:,:,0:3],
                                                                self.pointclouds_radius: np.ones(BATCH_SIZE),
                                                                edge_pl:np.expand_dims(edge,axis=0)})
                total_time +=time.time()-start_time
                norm_pl = np.zeros_like(pred)
                ##--------------visualize predicted point cloud----------------------
                if show:
                    f,axis = plt.subplots(3)
                    axis[0].imshow(pc_util.point_cloud_three_views(input[:,0:3],diameter=5))
                    axis[1].imshow(pc_util.point_cloud_three_views(pred[0,:,:],diameter=5))
                    axis[2].imshow(pc_util.point_cloud_three_views(gt[:,0:3], diameter=5))
                    plt.show()

                path = os.path.join(save_path, item.split('/')[-1][:-4]+".ply")
                # rgba =data_provider.convert_dist2rgba(pred_dist2,scale=10)
                # data_provider.save_ply(path, np.hstack((pred[0, ...],rgba,pred_dist2.reshape(NUM_ADDPOINT,1))))

                path = os.path.join(save_path, item.split('/')[-1][:-4] + "_gt.ply")
                rgba = data_provider.convert_dist2rgba(dist_gt[0],scale=5)
                data_provider.save_ply(path, np.hstack((pred[0, ...], rgba, dist_gt.reshape(NUM_ADDPOINT, 1))))

                path = path.replace(phase, phase+"_input")
                path = path.replace('xyz','ply')
                rgba = data_provider.convert_dist2rgba(pred_dist[0],scale=5)
                data_provider.save_ply(path, np.hstack((input[0],rgba,pred_dist.reshape(NUM_POINT,1))))
            print total_time/len(samples)

if __name__ == "__main__":
    np.random.seed(int(time.time()))
    tf.set_random_seed(int(time.time()))
    if PHASE=='train':
        assert not os.path.exists(os.path.join(MODEL_DIR, 'code/'))
        os.makedirs(os.path.join(MODEL_DIR, 'code/'))
        os.system('cp -r * %s' % (os.path.join(MODEL_DIR, 'code/')))  # bkp of model def
        network = Network()
        network.build_graph(is_training=True)
        network.train()
        LOG_FOUT.close()
    else:
        network = Network()
        BATCH_SIZE = 1
        NUM_EDGE = 1000
        network.build_graph(is_training=False)
        network.test_hierarical_prediction()
