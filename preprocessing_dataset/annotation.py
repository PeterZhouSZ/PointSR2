import os
from glob import glob
from subprocess import Popen

import numpy as np

from normalized import Mesh
from prepare_data import Mesh


def read_edge_from_offline(path):
    name = path.split()[-1][:-4]
    f = open(path,'r')
    lines = f.readlines()
    f.close()
    verts = []
    for item in lines:
        if item[0]=='v':
            XYZ = item.split()[1:]
            verts.append([float(XYZ[0]), float(XYZ[1]), float(XYZ[2])])
    verts = np.asarray(verts)

    edges = []
    points = []
    for item in lines:
        if item[0]=='l':
            id = item.split()[1:]
            for ii in xrange(len(id)-1):
                s  = verts[int(id[ii])-1]
                e = verts[int(id[ii+1])-1]
                edges.append(np.concatenate([s,e]))
                for iter in np.arange(0, 1.01, 0.01):
                    point = s+iter*(e-s)
                    points.append(point)
    edges = np.asarray(edges)
    points = np.asarray(points)
    return edges, points
    np.savetxt(name+'_edge.xyz',edges,fmt='%.6f')
    np.savetxt(name+'_edgepoint.xyz', points, fmt='%.6f')

def get_box_center(box):
    # box (6,4,3)
    center = np.mean(box,axis=[0,1])
    return center

def pointInBox(boxes,point,r=0.1):
    # boxes (n,6,4,3)
    # point (x,y,z)
    for box in boxes:
        if np.sum((get_box_center(box)-point)**2)<r**2:
            return True
    else:
        return False


def preprocessing_annotation_file(sharpedge_path):
    name = sharpedge_path.split('/')[-1][:-16]
    obj_path = '/home/lqyu/server/proj49/PointSR_data/virtual scan/Model_result_5/'+name+"_model.obj"
    off_path = '/home/lqyu/server/proj49/PointSR_data/virtual scan/mesh/'+name+".off"
    softedge_path = '/home/lqyu/server/proj49/PointSR_data/virtual scan/Model_result_5/'+name+"_soft_edges.obj"

    #convert obj to off
    cmd = '''meshlabserver -i '%s' -o '%s' '''%(obj_path,off_path)
    print cmd
    sts = Popen(cmd,shell=True).wait()
    if sts:
        print "Cannot convert %s"%(obj_path)
    m = Mesh()
    m.loadFromOffFile(off_path)
    m.write2OffFile(off_path)

    ##
    sharpedges, sharppoints = read_edge_from_offline(sharpedge_path)
    sharpedges[:,0:3] = (sharpedges[:,0:3]-m.centroid)/m.furthest_dist
    sharpedges[:,3:6] = (sharpedges[:,3:6]-m.centroid)/m.furthest_dist
    sharppoints=(sharppoints-m.centroid)/m.furthest_dist
    np.savetxt('/home/lqyu/server/proj49/PointSR_data/virtual scan/mesh_edge/'+name+'_sharpedge.xyz',sharpedges,fmt='%.6f')
    np.savetxt('/home/lqyu/server/proj49/PointSR_data/virtual scan/mesh_edgepoint/' + name + '_sharpedgepoint.xyz', sharppoints, fmt='%.6f')

    if os.path.exists(softedge_path):
        softedges, softpoints = read_edge_from_offline(softedge_path)
        softedges[:,0:3] = (softedges[:, 0:3] - m.centroid) / m.furthest_dist
        softedges[:, 3:6] = (softedges[:,3:6] - m.centroid) / m.furthest_dist
        softpoints = (softpoints - m.centroid) / m.furthest_dist
        np.savetxt('/home/lqyu/server/proj49/PointSR_data/virtual scan/mesh_edge/' + name + '_softedge.xyz', softedges, fmt='%.6f')
        np.savetxt('/home/lqyu/server/proj49/PointSR_data/virtual scan/mesh_edgepoint/' + name + '_softedgepoint.xyz', softpoints, fmt='%.6f')

def preprocessing_part_file(off_path):
    if '-' in off_path.split('/')[-1]:
        name = off_path.split('/')[-1].split('-')[0]
    else:
        name = off_path.split('/')[-1].split('.')[0]
    edge_path = '/home/lqyu/server/proj49/PointSR_data/virtualscan/mesh_edge/'+name+'_softedge.xyz'
    edgepoint_path = '/home/lqyu/server/proj49/PointSR_data/virtualscan/mesh_edgepoint/' + name + '_softedgepoint.xyz'
    
    m = Mesh()
    m.loadFromOffFile(off_path,is_remove_reducent=True,is_normalized=True)
    m.write2OffFile(off_path)

    softedges = np.loadtxt(edge_path)
    softpoints = np.loadtxt(edgepoint_path)
    softedges[:,0:3] = (softedges[:, 0:3] - m.centroid) / m.furthest_dist
    softedges[:, 3:6] = (softedges[:,3:6] - m.centroid) / m.furthest_dist
    softpoints = (softpoints - m.centroid) / m.furthest_dist

    np.savetxt(off_path[:-4]+'_edge.xyz', softedges, fmt='%.6f')
    np.savetxt(off_path[:-4]+'_edgepoint.xyz', softpoints, fmt='%.6f')


def convertX2off():
    file1 = glob('/home/lqyu/data_select/zip/*.zip')
    for id,item in enumerate(file1):
        save_path = '/home/lqyu/data_select/off/'+str(id)+'.off'
        #print save_path
        cmd1 = """unzip '%s' -d '%d'"""%(item, id)
        cmd2 = '''meshlabserver -i '%d/model.dae' -o '%s' '''%(id, save_path)
        print cmd1
        print cmd2
        sts = Popen(cmd1, shell=True).wait()
        sts = Popen(cmd2, shell=True).wait()

def merge_soft_and_sharp_edge():
    file = glob('/home/lqyu/server/proj49/PointSR_data/virtual scan/mesh_edge/*_sharpedge.xyz')
    for item in file:
        data1 = np.loadtxt(item)
        softedge_path = item.replace('sharpedge','softedge')
        if os.path.exists(softedge_path):
            data2 = np.loadtxt(softedge_path)
            data1 = np.concatenate([data1,data2])
        edge_path = item.replace('_sharpedge','_edge')
        np.savetxt(edge_path,data1,fmt='%.6f')

def extractcureedge(path):
    edge,edgepoint = read_edge_from_offline(path)
    path = path[:-9]+'.obj'
    path = path.replace('mesh_edge_obj','mesh_edge_curve')
    if os.path.exists(path):
        curveedge, _ = read_edge_from_offline(path)
    else:
        curveedge = np.zeros((0,3))


    strightedge = []
    for item1 in edge:
        mask = False
        for item in curveedge:
            if np.all(item1==item):
                mask = True
                break
        if mask==False:
            strightedge.append(item1)
    strightedge = np.asarray(strightedge)

    strightedgepoint = []
    for item in strightedge:
        for iter in np.arange(0, 1.01, 0.01):
            point = item[0:3] + iter * (item[3:6] - item[0:3])
            strightedgepoint.append(point)
    strightedgepoint = np.asarray(strightedgepoint)

    path = path.replace('mesh_edge_curve', 'mesh_edge_straight')
    path = path[:-4]
    np.savetxt(path+'_edge.xyz',strightedge,fmt='%.6f')
    np.savetxt(path+'_edgepoint.xyz', strightedgepoint, fmt='%.6f')

if __name__ == '__main__':
    # # convertX2off()
    # # file = glob('/home/lqyu/server/proj49/PointSR_data/CAD_imperfect/mesh_curve_edge/7.obj')
    # # for item in file:
    # #     read_edge_from_offline(item)
    # read_annotation_boxes('/home/lqyu/server/proj49/PointSR_data/CAD_imperfect/corner/7.obj')
    #
    #
    # file = glob('/home/lqyu/server/proj49/PointSR_data/virtualscan/mesh_soft/*.off')
    # for item in file:
    #     preprocessing_part_file(item)

    # merge_soft_and_sharp_edge()
    # file = glob('/home/lqyu/server/proj49/PointSR_data/virtual scan/Model_result_5/*_sharp_edges.obj')
    # print len(file)
    # for item in file:
    #     preprocessing_annotation_file(item)

    # file1 = glob('/home/lqyu/server/proj49/PointSR_data/Edgedirection/polyhedron_stl/*/*.stl')
    # file2 = glob('/home/lqyu/server/proj49/PointSR_data/Edgedirection/polyhedron_stl/*/*.STL')
    # file1 +=file2
    # file2 = glob('/home/lqyu/server/proj49/PointSR_data/Edgedirection/polyhedron_stl/*/*/*.stl')
    # file1 +=file2
    # file2 = glob('/home/lqyu/server/proj49/PointSR_data/Edgedirection/polyhedron_stl/*/*/*.STL')
    # file1 +=file2
    #
    # for id, item in enumerate(file1):
    #     save_path = '/home/lqyu/server/proj49/PointSR_data/Edgedirection/mesh/' + str(id) + '.off'
    #     # print save_path
    #     cmd2 = '''meshlabserver -i '%s' -o '%s' ''' % (item, save_path)
    #     print cmd2
    #     sts = Popen(cmd2, shell=True).wait()
    from GKNN import sampling_from_edge
    # file1 = glob('/home/lqyu/server/proj49/PointSR_data/Edgedirection/mesh2/*.off')
    # for item in file1:
    #     save_edge = '/home/lqyu/server/proj49/PointSR_data/Edgedirection/edge'
    #     save_edgepoint = '/home/lqyu/server/proj49/PointSR_data/Edgedirection/edgepoint'
    #     cmd2 = """/home/lqyu/server/proj49/third_party/EdgeSampling/build_local/EdgeSampling %s %s %s"""%(item,save_edge,save_edgepoint)
    #     print cmd2
    #     sts = Popen(cmd2, shell=True).wait()

    file1 = glob('/home/lqyu/server/proj49/PointSR_data/Edgedirection/edge/*_edge.xyz')
    for item in file1:
        h = []
        v = []
        edge = np.loadtxt(item)
        for e in edge:
            direction = e[3:6]-e[0:3]
            if np.all(direction==0):
                continue
            inner = np.dot(direction,np.asarray([0,1,0]))/np.sqrt(np.sum(direction**2))
            if np.abs(inner)>np.cos(20/180.0*np.pi):
                v.append(e)
            elif np.abs(inner) <np.cos(70/180.0*np.pi):
                h.append(e)

        h = np.asarray(h)
        v = np.asarray(v)
        np.savetxt(item.replace('edge','v_edge')[:-9]+'_edge.xyz',v,fmt='%0.6f')
        if v.shape[0]!=0:
            np.savetxt(item.replace('edge', 'v_edge')[:-9] + '_edgepoint.xyz', sampling_from_edge(v), fmt='%0.6f')

        np.savetxt(item.replace('edge','h_edge')[:-9] + '_edge.xyz', h, fmt='%0.6f')
        if h.shape[0] != 0:
            np.savetxt(item.replace('edge', 'h_edge')[:-9] + '_edgepoint.xyz', sampling_from_edge(h), fmt='%0.6f')