import cv2
import numpy as np
import os.path
import itertools
import argparse

import constants

def find_red(rgb, hue_diff=constants.HSV_HUE_TOLERANCE,
             min_saturation=constants.HSV_SAT_MINIMUM,
             min_value=constants.HSV_VALUE_MINIMUM):
    """Finds the red regions in the given image.
    
    rgb is the image to search, stored in the format made by cv2.imread
    by default.

    The three optional arguments represent the amount of tolerance for
    off-red colors in the image.
    """
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, np.array([0, min_saturation, min_value]),
                            np.array([hue_diff, 255, 255]))
    mask+= cv2.inRange(hsv, np.array([180-hue_diff, min_saturation, min_value]),
                            np.array([180, 255, 255]))
    return mask // 255

def smooth_regions(mask, open=constants.SMOOTH_OPEN_SIZE,
                         dilate=constants.SMOOTH_DILATE_SIZE,
                         close=constants.SMOOTH_CLOSE_SIZE):
    """Removes random dots that get made, dilates existing regions, then
    mergers together regions which are very near each other
    """
    open_kernel = np.ones((open, open), np.uint8)
    dilate_kernel = np.ones((dilate, dilate), np.uint8)
    close_kernel = np.ones((close, close), np.uint8)
    return cv2.morphologyEx(cv2.dilate(cv2.morphologyEx(mask,
                                                        cv2.MORPH_OPEN,
                                                        open_kernel
                                                       ),
                                       dilate_kernel, iterations=2
                                      ),
                            cv2.MORPH_CLOSE, close_kernel
                           )

def find_polygons(mask, epsilon=constants.POLYGON_EPSILON):
    """Takes the given mask and finds a polygon that fits it well."""
    contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    polys = [cv2.approxPolyDP(cnt, cv2.arcLength(cnt, True)*epsilon, True)
             for cnt in contours]
    return polys

def convert_polygon_to_roi(poly):
    """Takes a polygon and outputs an ROI of it.

    This presently just takes the smallest straight bounding rectangle.
    Doing more complicated things will come later.
    """
    x_left, y_top = map(min, zip(*map(lambda x: x[0], poly)))
    x_right, y_bottom = map(max, zip(*map(lambda x: x[0], poly)))
    return [x_right-x_left, y_bottom-y_top, x_left, y_top]

def save_rois(rois, outfile, imagename, append=True):
    if os.path.exists(outfile) and append:
        f = open(outfile, 'a')
    else:
        f = open(outfile, 'w')
    f.write('%s\t%s\n' % (os.path.abspath(imagename),
                '\t'.join(map(lambda x: '%d,%d,%d,%d' % tuple(x), rois))))
    f.close()

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
    arg_parser.add_argument('-o', '--override',
                            dest='override',
                            action='store_const', const=False,
                            default=True,
                            help='Override the outfile if it already exists '
                                 '(default=append to the file)',
                           )
    arg_parser.add_argument('-f', '--frame',
                            dest='frame',
                            type=int,
                            default=1,
                            help='The frame number in the video to use for '
                                 'ROI detection (default=1)',
                           )
    args = arg_parser.parse_args()
    video = cv2.VideoCapture(args.video)
    for i in range(args.frame-1):
        if not video.read()[0]:
            arg_parser.error('The video only has %d frames.' % i)
    exists, frame = video.read()
    if not exists:
        arg_parser.error('The video only has %d frames.' % (args.frame-1))
    mask = smooth_regions(find_red(frame))
    rois = list(map(get_roi_from_rect_pair, pair_rects(find_rects(mask))))
    save_rois(rois, args.outfile, args.video, append=args.override)

if __name__ == '__main__':
    main()

