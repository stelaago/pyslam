"""
* This file is part of PYSLAM 
*
* Copyright (C) 2016-present Luigi Freda <luigi dot freda at gmail dot com> 
*
* PYSLAM is free software: you can redistribute it and/or modify
* it under the terms of the GNU General Public License as published by
* the Free Software Foundation, either version 3 of the License, or
* (at your option) any later version.
*
* PYSLAM is distributed in the hope that it will be useful,
* but WITHOUT ANY WARRANTY; without even the implied warranty of
* MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
* GNU General Public License for more details.
*
* You should have received a copy of the GNU General Public License
* along with PYVO. If not, see <http://www.gnu.org/licenses/>.
"""

import numpy as np
import time
import cv2
from enum import Enum

from frame import Frame, match_frames

import g2o
from map_point import MapPoint
from map import Map
from geom_helpers import triangulate, add_ones, poseRt
from pinhole_camera import Camera, PinholeCamera

kVerbose=True     
kRansacThresholdNormalized = 0.0003  # metric threshold used for normalized image coordinates 
kRansacProb = 0.999
kNumMinTriangulatedPoints = 50

class InitializerOutput(object):
    def __init__(self):    
        self.pts = None 
        self.f_cur = None 
        self.f_ref = None 
        self.idx_cur = None 
        self.idx_ref = None 

class Initializer(object):
    def __init__(self):
        self.mask_match = None
        self.mask_recover = None 

    def estimatePose(self, kpn_ref, kpn_cur):	     
        E, self.mask_match = cv2.findEssentialMat(kpn_cur, kpn_ref, focal=1, pp=(0., 0.), method=cv2.RANSAC, prob=kRansacProb, threshold=kRansacThresholdNormalized)                         
        _, R, t, self.mask_recover = cv2.recoverPose(E, kpn_cur, kpn_ref, focal=1, pp=(0., 0.))   
        return poseRt(R,t.T)  # Rrc,trc (with respect to 'ref' frame)         

    def triangulatePoints(self, pose_1w, pose_2w, kpn_1, kpn_2):
        # P1w = np.dot(K1,  M1w) # K1*[R1w, t1w]
        # P2w = np.dot(K2,  M2w) # K2*[R2w, t2w]
        # since we are working with normalized coordinates x_hat = Kinv*x, one has         
        P1w = pose_1w[:3,:] # [R1w, t1w]
        P2w = pose_2w[:3,:] # [R2w, t2w]

        point_4d_hom = cv2.triangulatePoints(P1w, P2w, kpn_1.T, kpn_2.T)
        point_4d = point_4d_hom / point_4d_hom[3]  

        if False: 
            point_reproj = P1w @ point_4d;
            point_reproj = point_reproj / point_reproj[2] - add_ones(kpn_1).T
            err = np.sum(point_reproj**2)
            print('reproj err: ', err)     

        #pts_3d = point_4d[:3, :].T
        return point_4d.T       

    def init(self, f_cur, f_ref, idx_cur, idx_ref, img_cur):
        print('├────────')        
        print('initializing frames ', f_cur.id, ', ', f_ref.id)
        Mrc = self.estimatePose(f_ref.kpsn[idx_ref], f_cur.kpsn[idx_cur])
        f_cur.pose = np.linalg.inv(poseRt(Mrc[:3, :3], Mrc[:3, 3]))  # [Rcr, tcr] w.r.t. ref frame 

        # remove outliers      
        mask_index = [ i for i,v in enumerate(self.mask_match) if v > 0] 
        print('num inliers: ', len(mask_index))
        idx_cur_inliers = idx_cur[mask_index]
        idx_ref_inliers = idx_ref[mask_index]

        # create a temp map for initializing 
        map = Map()
        map.add_frame(f_ref)        
        map.add_frame(f_cur)

        points4d = self.triangulatePoints(f_cur.pose, f_ref.pose, f_cur.kpsn[idx_cur_inliers], f_ref.kpsn[idx_ref_inliers])
        #pts4d = triangulate(f_cur.pose, f_ref.pose, f_cur.kpsn[idx_cur], f_ref.kpsn[idx_ref])

        new_pts_count, mask_points = map.add_points(points4d, None, f_cur, f_ref, idx_cur_inliers, idx_ref_inliers, img_cur, check_parallax=True)
        print("triangulated:      %d new points, %d matches" % (new_pts_count, len(idx_cur)))    
        err = map.optimize(verbose=False)
        print("pose opt err:   %f units of error" % err)         

        #reset points in frames 
        f_cur.reset_points()
        f_ref.reset_points()

        is_ok = new_pts_count > kNumMinTriangulatedPoints

        out = InitializerOutput()
        out.points4d = points4d[mask_points]
        out.f_cur = f_cur
        out.idx_cur = idx_cur_inliers[mask_points]        
        out.f_ref = f_ref 
        out.idx_ref = idx_ref_inliers[mask_points]

        print('├────────')        
        return out, is_ok