from networkx.algorithms.link_prediction import cn_soundarajan_hopcroft
import numpy as np
import cv2
from numpy.lib.function_base import append
import sknw
from skimage.morphology import skeletonize
import matplotlib.pyplot as plt
import matplotlib as mpl
import networkx as nx
from sklearn.cluster import KMeans
from cv2 import aruco
import argparse
import os
import bbox


def warp(frame, coord1):
    """
    Inputs
        frame -- query image to label
        coord1 -- ARTag coordinates for reference image, used as points of reference to apply homography
    Outputs
        M -- 3x3 transformation matrix, used to warp query image to closely match reference image
        result -- warped query image. We do this warping to consistently label ROIs.
    """
    h, w = frame.shape

    # Initialize parameters for ARTag detection
    aruco_dict = aruco.Dictionary_get(aruco.DICT_4X4_100)
    parameters = aruco.DetectorParameters_create()
    parameters.adaptiveThreshConstant = 20
    parameters.adaptiveThreshWinSizeMax = 20
    parameters.adaptiveThreshWinSizeStep = 6
    parameters.minMarkerPerimeterRate = .02
    parameters.polygonalApproxAccuracyRate = .15
    parameters.perspectiveRemovePixelPerCell = 10
    parameters.perspectiveRemoveIgnoredMarginPerCell = .3
    parameters.minDistanceToBorder = 0

    # Find ARTag coordinates in query image and reformat data to match reference coordinates
    corners, ids, rejectedImgPoints = aruco.detectMarkers(frame, aruco_dict, parameters=parameters)
    avg = [np.average(x, axis = 1) for x in corners]
    frame_markers = aruco.drawDetectedMarkers(frame.copy(), corners, ids, [0, 255, 0])
    flat_corners = [item for sublist in avg for item in sublist]
    flat_ids = [item for sublist in ids for item in sublist]
    pair = sorted(zip(flat_ids, flat_corners))

    """
    # Test
    plt.imshow(frame_markers)
    plt.show()"""

    # Warp query image using ARTag coordinates
    coord2 = np.array([x[1] for x in pair]).astype(int)

    M ,status = cv2.findHomography(coord2, coord1)
    result = cv2.warpPerspective(frame, M, (w,h))
    return M, result

def mask(frame):
    """
    Inputs
        result -- warped query image
    Outputs
        mask -- thresholded query image keeping only bright sections in the image, to isolate the tree
            structure from the background
    """
    thresh = 160 # Might need to adjust this number if lighting conditions call for it (lower for dimmer arenas)
    ret1,th1 = cv2.threshold(frame,thresh,255,cv2.THRESH_BINARY)

    # Clean up mask with morphological operations
    open_kernel = np.ones((8, 8), np.uint8)
    dilate_kernel = np.ones((6, 6), np.uint8)
    close_kernel = np.ones((6, 6), np.uint8)
    mask = cv2.morphologyEx(th1, cv2.MORPH_OPEN, open_kernel)
    mask = cv2.dilate(mask, dilate_kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)
    return mask

def nodes(mask):
    """
    Inputs
        mask -- thresholded query image
    Outputs
        new_node_centers -- coordinates of each branching point, which is the center of each roi
    """
    # Skeletonize tree and build graph network
    skeleton = skeletonize(mask//255).astype(np.uint16)
    graph = sknw.build_sknw(skeleton)
    nodes = graph.nodes()
    node_centers = np.array([nodes[i]['o'] for i in nodes])

    # Filter out nodes at tips of branches, keeping only nodes that define the centers of each ROI
    copy = graph.copy()
    for i in range(len(node_centers)):
        conn = [n for n in graph.neighbors(i)]
        if len(conn) < 3:
            copy.remove_node(i)
    new_nodes = copy.nodes()
    new_node_centers = np.array([new_nodes[i]['o'] for i in new_nodes]).astype(int)

    return new_node_centers

def centers(reference, ps):
    """
    Inputs
        reference -- coordinates of each roi center in the reference image
        query -- detected coordinates of each roi center in the query image
    Outputs
        newpoints -- reordered query coordinates to match ordering of reference. This allows for consistent
            labelling.
    """
    # Find center point in query that is closest to the center point in reference
    newpoints = []
    for i in range(len(reference)):
        min_dist = 10000
        index = None
        for j in range(len(ps)):
            dist = np.sqrt(np.abs((reference[i][0] - ps[j][0])**2 + (reference[i][1] - ps[j][1])**2))
            if (dist < min_dist):
                min_dist = dist
                index = j
        newpoints.append(ps[index])
    newpoints = np.array(newpoints)
    return newpoints    

def contour(mask):
    """
    Inputs 
        mask -- thresholded query image
    Outputs
        cont -- rather than a skeletonized representation, return a contour image of the tree.
            We will use this to find the vertices of the rois
    """
    # Find and draw only the largest contour in image. This will be the tree structure
    cont = np.zeros_like(mask)
    contours, heir = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    contours = max(contours, key = cv2.contourArea)
    cv2.drawContours(cont, contours, -1, [255,255,255])
    return cont


def vertices(cont, newpoints, Dict):
    """
    Inputs
        cont -- contour image of tree structure
        newpoints -- reordered centers of query rois
        Dict -- dictionary mapping current labelling to match lab's labelling
    Outputs
        verts -- vertices of each roi, consistently ordered
    """
    #h, w = frame.shape
    conn = []
    for i in range(len(newpoints)):
        # Points of intersection between circle and contour image represent vertices of an roi
        circle = np.zeros_like(cont)
        cv2.circle(circle, (newpoints[i, 1], newpoints[i, 0]), 40, [255,255,255], 2)
        inter = cv2.bitwise_and(cont, circle)
        index = np.array(cv2.findNonZero(inter))
        index = np.array([index[i][0] for i in range(len(index))])
        # At times one point of intersection will be detected as two closely positioned points.
        # Use k means to ensure we get the correct number of vertices.
        kmeans = KMeans(n_clusters=6).fit(index)
        centers = np.array(kmeans.cluster_centers_).astype(int)
        # Connect vertices to form a convex polygon
        poly = cv2.convexHull(centers)
        poly = np.array([x[0] for x in poly])

        # Find largest edge defined by the vertices, and reorder vertices so that edge is first
        d = np.diff(poly, axis=0, append=poly[0:1])
        segdists = np.sqrt((d ** 2).sum(axis=1))
        index = np.argmax(segdists)
        roll = np.roll(poly, -index, axis = 0)

        # Reorder right junctions so they have the same labelling as left junctions
        if Dict[i] in {5,3,0,2,6,50,32,20,212,211,60,41,42,121,122,111,112}:
            roll = np.roll(roll, 2, axis = 0)
        conn.append(roll)
    conn = np.array(conn)
    return conn

def main():
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument('video',
                            type=str,
                            help='The path to a video with ROIs to detect.',
                           )
    arg_parser.add_argument('outfile',
                            type=str,
                            help='The path to a file in which to write the '
                                 'detected ROIs.',
                           )
    arg_parser.add_argument('-f', '--frame',
                            dest='frame',
                            type=int,
                            default=1,
                            help='The frame number in the video to use for '
                                 'ROI detection (default=1)',
                           )

    args = arg_parser.parse_args()
    # Read in first frame of video as an image
    if not os.path.isfile(args.video):
        arg_parser.error(f'{args.video} is not a valid file.')
    video = cv2.VideoCapture(args.video)
    ret, frame = video.read()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if not ret:
        arg_parser.error('The video only has {} frames.'.format(args.frame-1))

    # Load in relevant reference coordinates
    coord1 = np.array(np.loadtxt("templates/tag_coordinates.txt")).astype(int)
    reference = np.array(np.loadtxt("templates/center_coordinates.txt")).astype(int)
    Dict = {0:42, 1:122, 2:121, 3:41, 4:12, 5:40, 6:112, 7:8, 8:11, 9:6, 10:10, 11:4, 12:111, 13:2, 14:60, 15:0, 16:1, 17:3, 18:20, 19:7, 20:5, 21:211, 22:31, 23:21, 24:22, 25:30, 26:50, 27:212, 28:222, 29:221, 30:32}
    
    M, result = warp(gray, coord1)
    frame_mask = mask(result)
    query = nodes(frame_mask)
    newpoints = centers(reference, query)

    """# Testing
    print(newpoints)
    for i in range(len(newpoints)):
            cv2.circle(result,(newpoints[i][1],newpoints[i][0]),3,[255,0,0],3)
    plt.imshow(result)
    plt.show()"""


    cont = contour(frame_mask)
    verts = vertices(cont, newpoints, Dict)

    # Undo transformation to get vertices coordinates in original frame
    pts2 = np.array(verts, np.float32)
    polys = np.array(cv2.perspectiveTransform(pts2, np.linalg.pinv(M))).astype(int)

    """
    # Testing
    print(polys)
    for i in range(len(polys)):
        for j in range(6):
            cv2.circle(frame,(polys[i][j][0],polys[i][j][1]),3,[255,0,0],3)
    plt.imshow(frame)
    plt.show()"""

    # Save vertices to outfile
    rois = [bbox.BBox.from_verts(poly, 3) for poly in polys]
    bbox.save_rois(rois, args.outfile)

if __name__ == '__main__':
    main()