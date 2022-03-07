import os
import math

import torch
import numpy as np

from torch.utils.data import Dataset
import networkx as nx
from tqdm import tqdm


def anorm(p1,p2): 
    NORM = math.sqrt((p1[0]-p2[0])**2+ (p1[1]-p2[1])**2)
    if NORM ==0:
        return 0
    return 1/(NORM)
                
def seq_to_graph(seq_,seq_rel,norm_lap_matr = True):
    # num,2,len
    seq_ = seq_.squeeze()
    seq_rel = seq_rel.squeeze()
    seq_len = seq_.shape[2]
    max_nodes = seq_.shape[0]

    
    V = np.zeros((seq_len,max_nodes,2))
    A = np.zeros((seq_len,max_nodes,max_nodes))
    for s in range(seq_len): # time_length
        step_ = seq_[:,:,s]
        step_rel = seq_rel[:,:,s]
        for h in range(len(step_)):  # num_person
            V[s,h,:] = step_rel[h] # seq,node,2
            A[s,h,h] = 1 # seq,node,node
            for k in range(h+1,len(step_)):
                l2_norm = anorm(step_rel[h],step_rel[k])
                A[s,h,k] = l2_norm
                A[s,k,h] = l2_norm # 对角线1 其余l2倒数
        if norm_lap_matr: 
            G = nx.from_numpy_matrix(A[s,:,:])
            A[s,:,:] = nx.normalized_laplacian_matrix(G).toarray()
            
    return torch.from_numpy(V).type(torch.float),\
           torch.from_numpy(A).type(torch.float)


def poly_fit(traj, traj_len, threshold):
    """
    Input:
    - traj: Numpy array of shape (2, traj_len)
    - traj_len: Len of trajectory
    - threshold: Minimum error to be considered for non linear traj
    Output:
    - int: 1 -> Non Linear 0-> Linear
    """
    t = np.linspace(0, traj_len - 1, traj_len)
    res_x = np.polyfit(t, traj[0, -traj_len:], 2, full=True)[1]
    res_y = np.polyfit(t, traj[1, -traj_len:], 2, full=True)[1]
    if res_x + res_y >= threshold:
        return 1.0
    else:
        return 0.0
def read_file(_path, delim='\t'):
    data = []
    if delim == 'tab':
        delim = '\t'
    elif delim == 'space':
        delim = ' '
    with open(_path, 'r') as f:
        for line in f:
            line = line.strip().split(delim)
            line = [float(i) for i in line]
            data.append(line)
    return np.asarray(data)


class TrajectoryDataset(Dataset):
    """Dataloder for the Trajectory datasets"""
    def __init__(
        self, data_dir, obs_len=8, pred_len=12, skip=1, threshold=0.002,
        min_ped=1, delim='\t',norm_lap_matr = True,
        checkpoint_dir=checkpoint_dir):
        """
        Args:
        - data_dir: Directory containing dataset files in the format
        <frame_id> <ped_id> <x> <y>
        - obs_len: Number of time-steps in input trajectories
        - pred_len: Number of time-steps in output trajectories
        - skip: Number of frames to skip while making the dataset
        - threshold: Minimum error to be considered for non linear traj
        when using a linear predictor
        - min_ped: Minimum number of pedestrians that should be in a seqeunce
        - delim: Delimiter in the dataset files
        """
        super(TrajectoryDataset, self).__init__()

        self.max_peds_in_frame = 0
        self.data_dir = data_dir
        self.obs_len = obs_len
        self.pred_len = pred_len
        self.skip = skip
        self.seq_len = self.obs_len + self.pred_len
        self.delim = delim
        self.norm_lap_matr = norm_lap_matr # True

        all_files = os.listdir(self.data_dir)
        all_files = [os.path.join(self.data_dir, _path) for _path in all_files]
        num_peds_in_seq = []
        seq_list = []
        seq_list_rel = []
        loss_mask_list = []
        non_linear_ped = []
        for path in all_files:
            if 'graph_data.dat' in path:
                continue
            data = read_file(path, delim)
            frames = np.unique(data[:, 0]).tolist()
            frame_data = []
            for frame in frames:
                frame_data.append(data[frame == data[:, 0], :])
            num_sequences = int(
                math.ceil((len(frames) - self.seq_len + 1) / skip))

            for person_idx in range(0, num_sequences * self.skip + 1, skip):
                curr_seq_data = np.concatenate(
                    frame_data[person_idx:person_idx + self.seq_len], axis=0)
                peds_in_curr_seq = np.unique(curr_seq_data[:, 1])
                self.max_peds_in_frame = max(self.max_peds_in_frame,len(peds_in_curr_seq))
                curr_seq_rel = np.zeros((len(peds_in_curr_seq), 2,
                                         self.seq_len))
                curr_seq = np.zeros((len(peds_in_curr_seq), 2, self.seq_len))
                curr_loss_mask = np.zeros((len(peds_in_curr_seq),
                                           self.seq_len))
                num_peds_considered = 0
                _non_linear_ped = []
                for _, ped_id in enumerate(peds_in_curr_seq):
                    curr_ped_seq = curr_seq_data[curr_seq_data[:, 1] ==
                                                 ped_id, :]
                    curr_ped_seq = np.around(curr_ped_seq, decimals=4)
                    pad_front = frames.index(curr_ped_seq[0, 0]) - person_idx
                    pad_end = frames.index(curr_ped_seq[-1, 0]) - person_idx + 1
                    if pad_end - pad_front != self.seq_len:
                        continue
                    curr_ped_seq = np.transpose(curr_ped_seq[:, 2:])
                    curr_ped_seq = curr_ped_seq
                    # Make coordinates relative
                    rel_curr_ped_seq = np.zeros(curr_ped_seq.shape)
                    rel_curr_ped_seq[:, 1:] = \
                        curr_ped_seq[:, 1:] - curr_ped_seq[:, :-1]
                    _idx = num_peds_considered
                    curr_seq[_idx, :, pad_front:pad_end] = curr_ped_seq
                    curr_seq_rel[_idx, :, pad_front:pad_end] = rel_curr_ped_seq
                    # Linear vs Non-Linear Trajectory
                    _non_linear_ped.append(
                        poly_fit(curr_ped_seq, pred_len, threshold))
                    curr_loss_mask[_idx, pad_front:pad_end] = 1
                    num_peds_considered += 1

                if num_peds_considered > min_ped:
                    non_linear_ped += _non_linear_ped
                    num_peds_in_seq.append(num_peds_considered) # bs,num_person
                    loss_mask_list.append(curr_loss_mask[:num_peds_considered]) # bs,num_person,time_length
                    seq_list.append(curr_seq[:num_peds_considered]) # bs,num_person,2,time_length
                    seq_list_rel.append(curr_seq_rel[:num_peds_considered]) # bs,num_person,2,time_length

        self.num_seq = len(seq_list)
        seq_list = np.concatenate(seq_list, axis=0)
        seq_list_rel = np.concatenate(seq_list_rel, axis=0)
        loss_mask_list = np.concatenate(loss_mask_list, axis=0)
        non_linear_ped = np.asarray(non_linear_ped)

        # Convert numpy -> Torch Tensor
        self.obs_traj = torch.from_numpy(
            seq_list[:, :, :self.obs_len]).type(torch.float)
        self.pred_traj = torch.from_numpy(
            seq_list[:, :, self.obs_len:]).type(torch.float)
        self.obs_traj_rel = torch.from_numpy(
            seq_list_rel[:, :, :self.obs_len]).type(torch.float)
        self.pred_traj_rel = torch.from_numpy(
            seq_list_rel[:, :, self.obs_len:]).type(torch.float)
        self.loss_mask = torch.from_numpy(loss_mask_list).type(torch.float)
        self.non_linear_ped = torch.from_numpy(non_linear_ped).type(torch.float)
        cum_start_idx = [0] + np.cumsum(num_peds_in_seq).tolist()
        self.seq_start_end = [
            (start, end)
            for start, end in zip(cum_start_idx, cum_start_idx[1:])
        ]
        # Warning: this step is very time-consuming, adapted to save/load once for all
        # Convert to Graphs
        graph_data_path = os.path.join(self.data_dir, 'graph_data.dat')
        if not os.path.exists(graph_data_path):
            # process graph data from scratch
            self.v_obs = []
            self.A_obs = []
            self.v_pred = []
            self.A_pred = []
            # print("Processing Data .....")
            log_file = open(os.path.join(checkpoint_dir, "-info.txt"), "w")
            log_file.write("Processing Data .....")
            log_file.close()
            pbar = tqdm(total=len(self.seq_start_end))
            for ss in range(len(self.seq_start_end)):
                pbar.update(1)

                start, end = self.seq_start_end[ss]

                v_,a_ = seq_to_graph(self.obs_traj[start:end,:],self.obs_traj_rel[start:end, :],self.norm_lap_matr)
                self.v_obs.append(v_.clone())
                self.A_obs.append(a_.clone())
                v_,a_=seq_to_graph(self.pred_traj[start:end,:],self.pred_traj_rel[start:end, :],self.norm_lap_matr)
                self.v_pred.append(v_.clone())
                self.A_pred.append(a_.clone())
            pbar.close()
            graph_data = {'v_obs': self.v_obs, 'A_obs': self.A_obs, 'v_pred': self.v_pred, 'A_pred': self.A_pred}
            torch.save(graph_data, graph_data_path)
        else:
            graph_data = torch.load(graph_data_path)
            self.v_obs, self.A_obs, self.v_pred, self.A_pred = graph_data['v_obs'], graph_data['A_obs'], graph_data['v_pred'], graph_data['A_pred']
            log_file = open(os.path.join(checkpoint_dir, "-info.txt"), "w")
            log_file.write('Loaded pre-processed graph data at {:s}.'.format(graph_data_path))
            log_file.close()
            # print('Loaded pre-processed graph data at {:s}.'.format(graph_data_path))

        # prepare safe trajectory mask
        self.safe_traj_masks = [] #[bs,num_person]
        for batch_idx in range(len(self.seq_start_end)):
            start, end = self.seq_start_end[batch_idx]
            pred_traj_gt = self.pred_traj[start:end, :]  # [num_person, 2, 12]

            num_person = pred_traj_gt.size(0)
            safety_gt = torch.zeros(num_person).bool()   # [num_person]
            label_tarj_all = pred_traj_gt.permute(0, 2, 1).cpu().numpy()  # [num_person, 12, 2]
            for person_idx in range(num_person):
                label_traj_primary = label_tarj_all[person_idx]
                cur_traj_col_free = np.logical_not(compute_col(label_traj_primary, label_tarj_all).max())
                safety_gt[person_idx] = True if cur_traj_col_free else False
            self.safe_traj_masks.append(safety_gt)

    def __len__(self):
        return self.num_seq

    def __getitem__(self, index):
        start, end = self.seq_start_end[index]

        if 'train' in self.data_dir:
            out = [
                self.obs_traj[start:end, :], self.pred_traj[start:end, :],
                self.obs_traj_rel[start:end, :], self.pred_traj_rel[start:end, :],
                self.non_linear_ped[start:end], self.loss_mask[start:end, :],
                self.v_obs[index], self.A_obs[index],
                self.v_pred[index], self.A_pred[index], self.safe_traj_masks[index]
            ]
            # node, 2, 8/12    8/12,node,2 8/12,node,node
        else:
            out = [
                self.obs_traj[start:end, :], self.pred_traj[start:end, :],
                self.obs_traj_rel[start:end, :], self.pred_traj_rel[start:end, :],
                self.non_linear_ped[start:end], self.loss_mask[start:end, :],
                self.v_obs[index], self.A_obs[index],
                self.v_pred[index], self.A_pred[index]
            ]
        return out


def interpolate_traj(traj, num_interp=4):
    '''
    Add linearly interpolated points of a trajectory
    [num_person, 12, 2] 4
    '''
    sz = traj.shape
    dense = np.zeros((sz[0], (sz[1] - 1) * (num_interp + 1) + 1, 2)) # num_person* 56(11*5+1),2
    dense[:, :1, :] = traj[:, :1]

    for i in range(num_interp+1):
        ratio = (i + 1) / (num_interp + 1) #1/5 2/5 3/5 4/5 1
        dense[:, i+1::num_interp+1, :] = traj[:, 0:-1] * (1 - ratio) + traj[:, 1:] * ratio

    return dense


def compute_col(predicted_traj, predicted_trajs_all, thres=0.2):
    '''
    Input:
        predicted_trajs: predicted trajectory of the primary agents, [12, 2]
        predicted_trajs_all: predicted trajectory of all agents in the scene, [num_person, 12, 2]
    '''
    ph = predicted_traj.shape[0]
    num_interp = 4
    assert predicted_trajs_all.shape[0] > 1

    dense_all = interpolate_traj(predicted_trajs_all, num_interp) # (5, 56, 2)
    dense_ego = interpolate_traj(predicted_traj[None, :], num_interp) # (1, 56, 2)
    distances = np.linalg.norm(dense_all - dense_ego, axis=-1)  # [num_person, 12 * num_interp]
    mask = distances[:, 0] > 0  # exclude primary agent itself
    return (distances[mask].min(axis=0) < thres) # 56 (11*5+1) 
