import math
import pickle
from tqdm import tqdm

import gtsam
import matplotlib.pyplot as plt
from gtsam.utils.plot import plot_trajectory

from VAN_ex.code import arguments
from VAN_ex.code.ex5 import k_object
from VAN_ex.code.graph import Graph
from VAN_ex.code.tracking_database import TrackingDB
from ex6 import load
import numpy as np
import cv2
from ex3 import read_extrinsic_matrices
from ex4_v2 import rodriguez_to_mat, transformation_agreement, K
from ex5 import get_inverse
from ex1 import read_images
from ex6 import save

MATCHER = cv2.BFMatcher(normType=cv2.NORM_HAMMING, crossCheck=False)

MAHALANOBIS_THRESHOLD = 600
INLIERS_THRESHOLD = 200
LOCATION_SYMBOL = 'l'
CAMERA_SYMBOL = 'c'

relative_covariance_dict = dict()
cov_dijkstra_graph = Graph()

PATH_TO_DB = serialized_path = arguments.DATA_HEAD + "/docs/AKAZE/db/db_3359"
P0 = gtsam.Pose3(gtsam.Rot3(np.eye(3)), gtsam.Point3(np.zeros(3)))


def get_tracking_database(path_do_db_file=PATH_TO_DB):
    tracking_db = TrackingDB()
    tracking_db.load(path_do_db_file)
    return tracking_db


db = get_tracking_database(PATH_TO_DB)

from ex4_v2 import calc_ransac_iteration, triangulate_links, get_pixels_from_links, P, Q
from ex6 import calculate_relative_pose_cov


def q_7_4(pose_grpah, result, relative_pose, relative_cov, start_frame, end_frame):
    """
    add the result from q_7_3 that gives us the relative pose between the two frames.
    :param pose_grpah: the poses bundle graph
    :param result: the result, optimized estimates of the poses
    :param relative_pose: the relative pose calculated at q7_3
    :param relative_cov: the relative cov calculated at q7_3
    :param start_frame_symbol: the start frame symbol
    :param end_frame_symbol: the end frame symbol
    :return: the new result after optimization
    """
    noise_model = gtsam.noiseModel.Gaussian.Covariance(relative_cov)
    # noise_model = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.1, 0.1, 0.1, 0.1, 0.1, 0.1]))
    start_frame_symbol = gtsam.symbol(CAMERA_SYMBOL, start_frame)
    end_frame_symbol = gtsam.symbol(CAMERA_SYMBOL, end_frame)
    fac = gtsam.BetweenFactorPose3(start_frame_symbol, end_frame_symbol, relative_pose, noise_model)
    pose_grpah.add(fac)

    # calculating the global pose to add to the result estimate(that is going to be optimized)
    first_global_pose = result.atPose3(start_frame_symbol)
    global_pose = first_global_pose.transformPoseFrom(relative_pose)
    result.erase(end_frame_symbol)
    result.insert(end_frame_symbol, global_pose)
    # optimizing
    optimizer = gtsam.LevenbergMarquardtOptimizer(pose_grpah, result)
    new_result = optimizer.optimize()

    # add the edge to the dijkstra graph
    cov_dijkstra_graph.add_edge(start_frame, end_frame, relative_cov)
    # init_dijksra_graph_relative_covariance_dict(new_result, pose_grpah, relative_covariance_dict,
    #                                             cov_dijkstra_graph)

    return pose_grpah, new_result


def get_relative_consecutive_covariance(c1, c2, marginals):
    if c2 == c1:
        keys = gtsam.KeyVector()
        keys.append(c1)
        marginal_information = marginals.jointMarginalInformation(keys)
        inf_c2_giving_c1 = marginal_information.at(c1, c1)
        cov_c2_giving_c1 = np.linalg.inv(inf_c2_giving_c1)
        return cov_c2_giving_c1

    c2_number = int(gtsam.DefaultKeyFormatter(c2)[1:])
    if c2_number in relative_covariance_dict:
        return relative_covariance_dict[c2_number]
    # return relative_covariance_dict[c2_number]
    keys = gtsam.KeyVector()
    keys.append(c1)
    keys.append(c2)
    marginal_information = marginals.jointMarginalInformation(keys)
    inf_c2_giving_c1 = marginal_information.at(c2, c2)
    cov_c2_giving_c1 = np.linalg.inv(inf_c2_giving_c1)
    return cov_c2_giving_c1


def get_relative_covariance(index_list, marginals):
    cov_sum = None
    if len(index_list) == 1:
        c1 = gtsam.symbol('c', index_list[0])
        c_n_giving_c_i = get_relative_consecutive_covariance(c1, c1, marginals)
        return c_n_giving_c_i
    for i in range(len(index_list) - 1):
        c1 = gtsam.symbol('c', index_list[i])
        c2 = gtsam.symbol('c', index_list[i + 1])
        c_n_giving_c_i = get_relative_consecutive_covariance(c1, c2, marginals)
        if cov_sum is None:
            cov_sum = c_n_giving_c_i
        else:
            cov_sum = cov_sum + c_n_giving_c_i
    return cov_sum


def get_relative_pose(pose):
    pass


def calculate_mahalanobis_distance(c_n, c_i, result, relative_information):
    pose_c_n = result.atPose3(c_n)
    pose_c_i = result.atPose3(c_i)
    relative_pose = pose_c_n.between(pose_c_i)
    # relative_covariance = np.linalg.inv(relative_information)
    # relative_covariance = gtsam.noiseModel.Gaussian.Covariance(relative_covariance)
    # factor = gtsam.BetweenFactorPose3(c_n, c_i, P0, relative_covariance)
    # values = gtsam.Values()
    # values.insert(c_n, pose_c_n)
    # values.insert(c_i, pose_c_i)
    #
    # mahalanobis_distance = factor.error(values)
    # print(f"mahalanobis_distance:{mahalanobis_distance}")
    # return mahalanobis_distance
    delta = np.hstack([relative_pose.rotation().ypr(), relative_pose.translation()])
    mahalanobis_distance = delta @ relative_information @ delta
    # mahalanobis_distance = relative_pose @ relative_information @ relative_pose
    # print(f"mahalanobis_distance:{math.sqrt(mahalanobis_distance)}, determinant: {np.linalg.det(relative_information)} delta norm: {np.linalg.norm(delta)}")
    return math.sqrt(mahalanobis_distance)


def get_symbol(index):
    return gtsam.symbol('c', index)


def check_candidate(c_n_idx, c_i_idx, marginals, result, index_list):
    cur_index_list = cov_dijkstra_graph.get_shortest_path(index_list[c_i_idx], index_list[c_n_idx])
    c_n_giving_c_i = get_relative_covariance(cur_index_list, marginals)
    # c_n_giving_c_i = sum_covariance + get_relative_consecutive_covariance(gtsam.symbol('c', index_list[c_i_idx]),
    #                                                                       gtsam.symbol('c', index_list[c_n_idx]), marginals)
    relative_information = np.linalg.inv(c_n_giving_c_i)
    symbol_cn = get_symbol(index_list[c_n_idx])
    symbol_ci = get_symbol(index_list[c_i_idx])
    mahalanobis_distance = calculate_mahalanobis_distance(symbol_cn, symbol_ci, result, relative_information)

    return mahalanobis_distance


KEY_FRAME_GAP = 10


def get_good_candidates(c_n_index, marginals, result, index_list):
    candidates = []
    # camera_symbol = gtsam.symbol('c', camera_referance_index)
    last_index_to_check = c_n_index - KEY_FRAME_GAP
    for c_i_index in range(0, last_index_to_check, 1):
        # camera_i_symbol = gtsam.symbol('c', i)
        mahalanobis_distance = check_candidate(c_n_index, c_i_index, marginals, result, index_list)
        if mahalanobis_distance < MAHALANOBIS_THRESHOLD:
            candidates.append(index_list[c_i_index])
        elif mahalanobis_distance > MAHALANOBIS_THRESHOLD * 10:
            c_i_index += 2

    return candidates


def get_path(c_n, c_i, result):
    return [index for index in range(c_i, c_n + 1)]


def load(base_filename):
    """ load TrackingDB to base_filename+'.pkl' file. """
    filename = base_filename + '.pkl'
    with open(filename, 'rb') as file:
        data = pickle.load(file)
    print('Bundles loaded from', filename)
    return data


def get_index_list(result):
    index_list = []
    for key in result.keys():
        cam_number = int(gtsam.DefaultKeyFormatter(key)[1:])
        index_list.append(cam_number)
    index_list.sort()
    return index_list


def init_dijksra_graph_relative_covariance_dict(result_without_closure, pose_graph_without_closure,
                                                cov_dict, dijkstra_graph):
    marginals = gtsam.Marginals(pose_graph_without_closure, result_without_closure)
    index_list = get_index_list(result_without_closure)
    for i in range(len(index_list) - 1):
        c1 = gtsam.symbol('c', index_list[i])
        c2 = gtsam.symbol('c', index_list[i + 1])
        c_n_giving_c_i = get_relative_consecutive_covariance(c1, c2, marginals)
        cov_dict[index_list[i + 1]] = c_n_giving_c_i
        dijkstra_graph.add_edge(index_list[i], index_list[i + 1], c_n_giving_c_i)

    cov_dict[index_list[0]] = marginals.marginalCovariance(gtsam.symbol('c', index_list[0]))
    return cov_dict, dijkstra_graph


def q_7_5_6(marginals, result_without_closure, pose_graph_without_closure):
    """
    Plot a graph of the location uncertainty size for the whole pose graph both with and
    without loop closures. The uncertainty size is defined as the standard deviation of the covariances
    :param result: the result
    :param result_without_closure: the result without closure
    :return: None
    """
    relative_covariance_dict_without_closure = dict()
    dijkstra_graph_without_closure = Graph()
    marginals_without_closure = gtsam.Marginals(pose_graph_without_closure, result_without_closure)
    rel_cov_before_loop_c, dijkstra_graph = init_dijksra_graph_relative_covariance_dict(result_without_closure,
                                                                                        pose_graph_without_closure,
                                                                                        relative_covariance_dict_without_closure,
                                                                                        dijkstra_graph_without_closure)

    index_list = get_index_list(result_without_closure)
    uncertinties_before = []
    for c_n in index_list[:]:
        cur_index_list = dijkstra_graph.get_shortest_path(index_list[0], c_n)
        rel_cov = get_relative_covariance(cur_index_list, marginals_without_closure)
        det = np.linalg.det(rel_cov)
        uncertinties_before.append(det)

    plt.figure()
    plt.plot(index_list, uncertinties_before, label='Uncertainty without loop closures')

    uncertinties = []
    for c_n in index_list:
        cur_index_list = cov_dijkstra_graph.get_shortest_path(index_list[0], c_n)
        det = np.linalg.det(get_relative_covariance(cur_index_list, marginals=marginals))
        uncertinties.append(det)

    plt.figure()
    plt.plot(index_list, uncertinties, label='Uncertainty with loop closures')
    plt.show()


def find_loops(data):
    pose_graph = data[0]
    result = data[1]
    marginals = gtsam.Marginals(pose_graph, result)
    index_list = get_index_list(result)
    init_dijksra_graph_relative_covariance_dict(result, pose_graph, relative_covariance_dict, cov_dijkstra_graph)
    # print(f"relative_covariance_lst: {relative_covariance_dict}")
    familiar_path = False
    frames_in_familiar_path = []
    for c_n_index in tqdm(range(len(index_list))):
        good_candidates = get_good_candidates(c_n_index, marginals, result, index_list)
        if len(good_candidates) > 0:
            camera_number = index_list[c_n_index]
            if familiar_path:
                frames_in_familiar_path.append((camera_number, good_candidates))
                continue
            # print(f"good_candidates for camera {c_n_index}: {good_candidates}")

            # best_candidate is the candidate with best inliers numbers
            best_candidate, matches = consensus_matches(camera_number, good_candidates, db)
            if best_candidate is not None:
                familiar_path = True
                # print(f"best_candidate for camera {camera_number}: {best_candidate}")
                # todo continue to and UPDATE THE GRAPH
                pose_graph, result = insert_to_pose_graph(camera_number, best_candidate,
                                                          matches, pose_graph, result)
                print(f"loop detected between camera {camera_number} and camera {best_candidate}")
        else:
            if len(frames_in_familiar_path) > 0:
                # print(f"end of path that started on camera {frames_in_familiar_path[0]} and ended in"
                #       f" {frames_in_familiar_path[-1]}")
                for camera_number, candidates in frames_in_familiar_path[::-1]:
                    best_candidate, matches = consensus_matches(camera_number, candidates, db)
                    if best_candidate is not None:
                        # insert the last frame that passes the best candidate matches test on the path
                        pose_graph, result = insert_to_pose_graph(camera_number, best_candidate,
                                                                  matches, pose_graph, result)
                        print(
                            f"end of familiar segment, loop closure between camera {camera_number} "
                            f"and camera {best_candidate}")
                        break
                familiar_path = False
                frames_in_familiar_path = []

            # if not familiar_path:
    return pose_graph, result

    # # print(f"good_candidates for camera {index_list[c_n_index]}: {good_candidates}")


def plot_graph_along(camera_number, pose_graph, result):
    marginals = gtsam.Marginals(pose_graph, result)
    plot_trajectory(camera_number, result, marginals=marginals,
                    title=f"graph plotted along the process after closure on"
                          f" camera {camera_number}, Q_7_5_2")


def insert_to_pose_graph(camera_number, best_candidate, matches, pose_graph, result):
    """
    insert to the pose graph and to the reult and dijkstra graph
    :param camera_number: the number of the camera(not symbol)
    :param best_candidate: the second frame number, not symbol
    :param dijkstra_graph: the dijkstra cov graph
    :param matches: the matches between the two frames
    :param pose_graph: pose graph
    :param result: result
    :return: pose graph and reult, updated
    """
    rel_pose, rel_cov = q_7_3(camera_number, best_candidate, pose_graph, result, matches, db)
    pose_graph, result = q_7_4(pose_graph, result, rel_pose, rel_cov, camera_number, best_candidate)
    # q7_5_2 todo change the plot so we wont plot when we take it out
    plot_graph_along(camera_number, pose_graph, result)

    return pose_graph, result


mat = read_extrinsic_matrices()[:]
cameras_locations2 = []
for cam in mat:
    rot = cam[:3, :3]
    t = cam[:3, 3]
    cameras_locations2.append(-rot.T @ t)
    # lst = [42, 51, 58, 65]


# plt.figure()
# plt.plot([x[0] for x in cameras_locations2], [x[2] for x in cameras_locations2], 'ro')
# plt.plot([cameras_locations2[i][0] for i in candid], [cameras_locations2[i][2] for i in candid], 'bo')
# plot_trajectory(2, result )

def ransac_pnp(matches_l_l, prev_links, cur_links):
    """ Perform RANSAC to find the best transformation"""
    ransac_iterations = 10000
    # print(f"ransac_iterations: {ransac_iterations}")

    filtered_links_cur = []
    filtered_links_prev = []

    for match in matches_l_l:
        link_index = match.trainIdx
        filtered_links_cur.append(cur_links[link_index])

        link_index = match.queryIdx
        filtered_links_prev.append(prev_links[link_index])

    # filtered_links_cur = cur_links
    # filtered_links_prev = prev_links

    points_3d = triangulate_links(filtered_links_prev, P, Q)

    prev_left_pix_values, prev_right_pix_values = get_pixels_from_links(filtered_links_prev)
    ordered_cur_left_pix_values, ordered_cur_right_pix_values = get_pixels_from_links(filtered_links_cur)

    prev_left_pix_values = np.array(prev_left_pix_values)
    prev_right_pix_values = np.array(prev_right_pix_values)
    ordered_cur_left_pix_values = np.array(ordered_cur_left_pix_values)
    ordered_cur_right_pix_values = np.array(ordered_cur_right_pix_values)

    diff_coeff = np.zeros((5, 1))
    best_inliers = 0
    best_T = None
    best_matches_idx = []

    for i in range(ransac_iterations):
        random_idx = np.random.choice(len(points_3d), 4, replace=False)
        random_world_points = points_3d[random_idx]
        # random_cur_l_pixels = np.array([(kp_l_cur[concensus_matches_cur_idx])[rand].pt for rand in random_idx])
        random_cur_l_pixels = ordered_cur_left_pix_values[random_idx]
        success, rotation_vector, translation_vector = cv2.solvePnP(random_world_points, random_cur_l_pixels, K,
                                                                    distCoeffs=diff_coeff, flags=cv2.SOLVEPNP_EPNP)

        if success:
            T = rodriguez_to_mat(rotation_vector, translation_vector)
        else:
            continue

        # points_agreed = transformation_agreement(T, points_3d, kp_l_prev, kp_r_prev, kp_l_cur, kp_r_cur,
        #                                          x_condition=True)

        points_agreed = transformation_agreement(T, points_3d, prev_left_pix_values, prev_right_pix_values,
                                                 ordered_cur_left_pix_values, ordered_cur_right_pix_values,
                                                 x_condition=False)

        num_inliers = np.sum(points_agreed)
        if num_inliers > best_inliers:
            best_inliers = num_inliers
            best_T = T
            best_matches_idx = np.where(points_agreed == True)[0]

    world_points = points_3d[best_matches_idx]
    # random_cur_l_pixels = np.array([(kp_l_cur[concensus_matches_cur_idx])[rand].pt for rand in random_idx])
    pixels = ordered_cur_left_pix_values[best_matches_idx]
    if len(best_matches_idx) < 4:
        return None, [], []
    success, rotation_vector, translation_vector = cv2.solvePnP(world_points, pixels, K,
                                                                distCoeffs=diff_coeff, flags=cv2.SOLVEPNP_EPNP)

    if success:
        T = rodriguez_to_mat(rotation_vector, translation_vector)
        inv_t = get_inverse(T)
        relative_pose = gtsam.Pose3(gtsam.Rot3(inv_t[:3, :3]), gtsam.Point3(inv_t[:3, 3]))
        return relative_pose, best_matches_idx, best_inliers
    else:
        return None, None, None


def check_candidate_match(reference_key_frame, candiate_keyframe, db: TrackingDB):
    keyframe1_features = db.features(reference_key_frame)
    keyframe1_links = db.all_frame_links(reference_key_frame)

    keyframe2_features = db.features(candiate_keyframe)
    keyframe2_links = db.all_frame_links(candiate_keyframe)

    # get matches
    matches_l_l = MATCHER.match(keyframe1_features, keyframe2_features)

    # project the points from keyframe1 to keyframe2
    T, best_matches_idx, best_inliers = ransac_pnp(matches_l_l, keyframe1_links, keyframe2_links)
    return [matches_l_l[i] for i in best_matches_idx]
    # matches = []
    # for i, match in enumerate(matches_l_l):
    #     link1 = keyframe1_links[match.queryIdx]
    #     link2 = keyframe2_links[match.trainIdx]
    #
    #     stereo_point1 = gtsam.StereoPoint2(link1.x_left, link1.x_right, link1.y)
    #     point_in_world = camera1.backproject(stereo_point1)
    #
    #     if point_in_world[2] < 0:
    #         continue
    #     try:
    #         point_to_cam2 = camera2.project(point_in_world)
    #     except:
    #         return []
    #
    #
    #     x_match_left = np.abs(point_to_cam2.uL() - link2.x_left) < 2
    #     y_match_left = np.abs(point_to_cam2.v() - link2.y) < 2
    #     left_match = x_match_left and y_match_left
    #
    #     x_match_right = np.abs(point_to_cam2.uR() - link2.x_right) < 2
    #     y_match_right = np.abs(point_to_cam2.v() - link2.y) < 2
    #     right_match = x_match_right and y_match_right
    #
    #     if left_match and right_match:
    #         matches.append(match)
    # return matches


def create_bundle(first_frame_idx, second_frame_idx, bundle_graph, result, inliers, db: TrackingDB):
    """ Create a factor graph for the given frames."""
    # get the first frame symobols
    first_frame_symbol = gtsam.symbol(CAMERA_SYMBOL, first_frame_idx)
    second_frame_symbol = gtsam.symbol(CAMERA_SYMBOL, second_frame_idx)

    # creating a graph and initial estimates
    graph = gtsam.NonlinearFactorGraph()
    initial_estimate = gtsam.Values()

    # calculating the first frame prior
    first_pose = gtsam.Pose3()
    first_pose_sigma = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.01, 0.01, 0.01, 0.01, 0.01, 0.01]))

    # adding the prior to the graph and to the initial estimates - it is relative so no real prior
    graph.add(gtsam.PriorFactorPose3(first_frame_symbol, first_pose, first_pose_sigma))
    initial_estimate.insert(first_frame_symbol, gtsam.Pose3())

    # calculating the relative cov and pose to the next pose
    marginals, relative_pose_second, relative_cov_second = (
        calculate_relative_pose_cov(first_frame_symbol, second_frame_symbol, bundle_graph, result))

    # insert initial estimates
    initial_estimate.insert(second_frame_symbol, relative_pose_second)

    # insert to the graph:
    # Add the relative pose factor to the pose graph
    noise_model = gtsam.noiseModel.Gaussian.Covariance(relative_cov_second)

    # Add the relative pose factor to the pose graph
    factor = gtsam.BetweenFactorPose3(first_frame_symbol, second_frame_symbol, relative_pose_second, noise_model)
    graph.add(factor)

    # Create the stereo camera
    gtsam_frame_1 = gtsam.StereoCamera(first_pose, k_object)
    # gtsam_frame_2 = gtsam.StereoCamera(relative_pose_second, k_object)

    first_frame_links = db.all_frame_links(first_frame_idx)
    second_frame_links = db.all_frame_links(second_frame_idx)

    # add the stereo factors to the graph
    for match in inliers:
        # first frame link insert to the graph
        location_symbol = gtsam.symbol(LOCATION_SYMBOL, match.queryIdx)
        first_link = first_frame_links[match.queryIdx]

        # Create the factor
        sigma = gtsam.noiseModel.Isotropic.Sigma(3, 1.0)
        graph.add(gtsam.GenericStereoFactor3D(gtsam.StereoPoint2(first_link.x_left, first_link.x_right, first_link.y),
                                              sigma, first_frame_symbol, location_symbol, k_object))

        # triangultes from the first frame, chosen arbitarly could be second frame...
        reference_triangulated_point = gtsam_frame_1.backproject(
            gtsam.StereoPoint2(first_link.x_left, first_link.x_right, first_link.y))

        assert reference_triangulated_point[2] > 0
        # insert to the initial estimate
        initial_estimate.insert(location_symbol, reference_triangulated_point)

        # second frame link insert to the graph
        second_link = second_frame_links[match.trainIdx]

        # Create the factor
        graph.add(
            gtsam.GenericStereoFactor3D(gtsam.StereoPoint2(second_link.x_left, second_link.x_right, second_link.y),
                                        sigma, second_frame_symbol, location_symbol, k_object))

    return graph, initial_estimate


def q_7_3(first_frame_idx, second_frame_idx, bundle_graph, result, inliers, db: TrackingDB):
    graph, initial_estimate = create_bundle(first_frame_idx, second_frame_idx, bundle_graph, result, inliers, db)
    # Optimize the factor graph
    optimizer = gtsam.LevenbergMarquardtOptimizer(graph, initial_estimate)
    result = optimizer.optimize()

    first_frame_symbol = gtsam.symbol(CAMERA_SYMBOL, first_frame_idx)
    second_frame_symbol = gtsam.symbol(CAMERA_SYMBOL, second_frame_idx)

    # now we need to extract the first two poses and their cov
    first_pose = result.atPose3(first_frame_symbol)
    second_pose = result.atPose3(second_frame_symbol)
    relative_pose = first_pose.between(second_pose)

    # Obtain marginal covariances directly
    keys = gtsam.KeyVector()
    keys.append(first_frame_symbol)
    keys.append(second_frame_symbol)

    marginals = gtsam.Marginals(graph, result)
    information = marginals.jointMarginalInformation(keys).fullMatrix()
    # Extract the relative covariance
    relative_cov = np.linalg.inv(information[-6:, -6:])

    return relative_pose, relative_cov


def consensus_matches(reference_key_frame, candidates_index_lst, data_base: TrackingDB):
    best_candidate = None
    best_matches = []

    # prev_links = data_base.all_frame_links(reference_key_frame)
    # todo sort the candidates by their mahalanobis distance
    for candidate in candidates_index_lst:
        # T, matches_idx,_ = ransac_pnp(matches_l_l, )
        matches = check_candidate_match(reference_key_frame, candidate, data_base)
        # print(f"number of inliers: {len(matches)}")
        if len(matches) > INLIERS_THRESHOLD:
            best_candidate = candidate
            best_matches = matches

            return best_candidate, best_matches

    # check the inliers percentage
    if len(best_matches) > 0:
        if len(best_matches) < INLIERS_THRESHOLD:
            best_candidate = None
            best_matches = []
    return best_candidate, best_matches


def get_camera_location_from_gtsam(pose: gtsam.Pose3):
    # final_cam_trans = pose.translation()
    # final_cam_rot = pose.rotation().matrix().T
    # final_cam_trans = -final_cam_rot @ final_cam_trans

    # return -pose.rotation().matrix() @ final_cam_trans
    return pose.translation()


def plot_matches(first_frame, second_frame):
    # plot the inliers
    inliers = check_candidate_match(first_frame, second_frame, db)
    query_idx_list = [match.queryIdx for match in inliers]
    all_matches = MATCHER.match(db.features(first_frame), db.features(second_frame))
    outliers = []
    for match in all_matches:
        if match.queryIdx not in query_idx_list:
            outliers.append(match)

    inliers_points_first = [db.all_frame_links(first_frame)[match.queryIdx] for match in inliers]
    outliers_points_first = [db.all_frame_links(first_frame)[match.queryIdx] for match in outliers]

    inliers_points_second = [db.all_frame_links(second_frame)[match.trainIdx] for match in inliers]
    outliers_points_second = [db.all_frame_links(second_frame)[match.trainIdx] for match in outliers]

    img1 = read_images(first_frame)[0]
    img2 = read_images(second_frame)[0]

    plt.figure()
    plt.subplot(2, 1, 1)
    plt.imshow(img1, cmap='gray')
    plt.scatter([point.x_left for point in inliers_points_first], [point.y for point in inliers_points_first], c='r',
                s=2)
    plt.scatter([point.x_left for point in outliers_points_first], [point.y for point in outliers_points_first], c='b',
                s=2)
    plt.title(f"first frame: {min(first_frame, second_frame)} inliers in red outliers in blue")

    plt.subplot(2, 1, 2)
    plt.imshow(img2, cmap='gray')
    plt.scatter([point.x_left for point in inliers_points_second], [point.y for point in inliers_points_second], c='r',
                s=2)
    plt.scatter([point.x_left for point in outliers_points_second], [point.y for point in outliers_points_second],
                c='b', s=2)
    plt.title(f"second frame: {max(first_frame, second_frame)} inliers in red outliers in blue")

    plt.show()


def get_locations_from_gtsam(result):
    locations = []
    index_list = get_index_list(result)
    for index in index_list:
        pose = result.atPose3(gtsam.symbol(CAMERA_SYMBOL, index))
        location = get_camera_location_from_gtsam(pose)
        # assert location == pose.translation()
        locations.append(location)
    return locations


def get_locations_ground_truths():
    all_transformations = read_extrinsic_matrices()
    cameras_locations2 = []
    for cam in all_transformations:
        rot = cam[:3, :3]
        t = cam[:3, 3]
        cameras_locations2.append(-rot.T @ t)
    return cameras_locations2


def plot_trajectory2D_ground_truth(result, str):
    cameras_locations2 = get_locations_ground_truths()
    locations = get_locations_from_gtsam(result)
    plt.figure()
    plt.plot([location[0] for location in locations], [location[2] for location in locations], 'r-')
    plt.plot([location[0] for location in cameras_locations2], [location[2] for location in cameras_locations2], 'b-')
    plt.title(f"q7_5_3 graph {str} red: estimated trajectory, blue: ground truth trajectory")
    plt.show()


# def plot_trajectory_2D(result):
#     locations = get_locations(result)
#     plt.figure()
#     plt.plot([location[0] for location in locations], [location[2] for location in locations], 'r-')
def q_7_5_5(result, result_without_closure):
    locations = get_locations_from_gtsam(result)
    locations_without_closure = get_locations_from_gtsam(result_without_closure)
    ground_truth_locations = get_locations_ground_truths()
    plt.figure()
    plt.plot([np.linalg.norm(locations[i] - ground_truth_locations[i]) for i in range(len(locations))], 'r-')
    plt.plot([np.linalg.norm(locations_without_closure[i] - ground_truth_locations[i]) for i in
              range(len(locations_without_closure))], 'b-')
    plt.title("q7_5_5 graph red: with closure, blue: without closure")

    plt.figure()
    plt.plot([np.linalg.norm(locations[i][1] - ground_truth_locations[i][1]) for i in range(len(locations))], 'r-')
    plt.plot([np.linalg.norm(locations_without_closure[i][1] - ground_truth_locations[i][1]) for i in
              range(len(locations_without_closure))], 'b-')
    plt.title("axis y, error")
    plt.show()


def q_7_5(pose_graph, result, data):
    pose_graph_without_closure = data[0]
    result_without_closure = data[1]
    ground_truth_locations = get_locations_ground_truths()

    # Q_7_5_2
    plot_graph_along(0, pose_graph_without_closure, result_without_closure)

    # q7_5_3
    plot_matches(1488, 42)

    # q7_5_4
    plot_trajectory2D_ground_truth(result_without_closure, "without closure, q7_5_4")
    plot_trajectory2D_ground_truth(result, "with closure, q7_5_4")

    # q7_5_5 plot a graph of the absolute error for the whole pose graph both with and without the loop closure
    q_7_5_5(result, result_without_closure)

    # q7_5_6 plot a graph of the location uncertainty  size for the whole pose graph both with and without the loop closure
    marginals = gtsam.Marginals(pose_graph, result)
    q_7_5_6(marginals, result_without_closure, pose_graph_without_closure)


if __name__ == '__main__':
    # load data
    path = arguments.DATA_HEAD + '/docs/pose_graph_result'
    data_list = load(path)

    init_dijksra_graph_relative_covariance_dict(data_list[1], data_list[0], relative_covariance_dict,
                                                cov_dijkstra_graph)

    # marginals = gtsam.Marginals(data_list[0], data_list[1])
    # q_7_5_6(marginals, data_list[1], data_list[0])

    # plt.show()
    # pg, res, relative_covariance_dict, cov_dijkstra_graph = load("updated_pose_graph_results")

    # data_list = []
    pg, res = find_loops(data_list)
    save((pg, res, relative_covariance_dict, cov_dijkstra_graph), "updated_pose_graph_results")

    q_7_5(pg, res, data_list)
    plot_trajectory(0, res, title="updated pose graph")
    # print("pipi")
    plt.show()
