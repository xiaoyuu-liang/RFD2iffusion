import numpy as np
import os
import torch
import torch.nn.functional as F
import scipy.io as scio
from tfdiff.params import AttrDict
from glob import glob
from torch.utils.data.distributed import DistributedSampler

# data_key='csi_data',
# gesture_key='gesture',
# location_key='location',
# orient_key='orient',
# room_key='room',
# rx_key='rx',
# user_key='user',
# receiver locations
map_positions = {
    1: (0.5, -0.5),  
    2: (1.4, -0.5), 
    3: (2.0, 0.0), 
    4: (-0.5, 0.5),
    5: (-0.5, 1.4),
    6: (0.0, 2.0),
    7: (0.5, 0.0),
    8: (1.4, 0.0),
    9: (1.0, 0.5),
    10: (1.0, 1.5),
    11: (2.0, 1.0),
    12: (2.0, 2.0),
}
tx_position = (0., 0.)  # transmitter location

def calculate_distance(p1, p2):
    return np.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)

def _nested_map(struct, map_fn):
    if isinstance(struct, tuple):
        return tuple(_nested_map(x, map_fn) for x in struct)
    if isinstance(struct, list):
        return [_nested_map(x, map_fn) for x in struct]
    if isinstance(struct, dict):
        return {k: _nested_map(v, map_fn) for k, v in struct.items()}
    return map_fn(struct)


class WiFiDataset(torch.utils.data.Dataset):
    def __init__(self, paths):
        super().__init__()
        self.filenames = []
        for path in paths:
            self.filenames += glob(f'{path}/**/user*.mat', recursive=True)

    def __len__(self):
        return len(self.filenames)

    def __getitem__(self, idx):
        cur_filename = self.filenames[idx]
        cur_sample = scio.loadmat(cur_filename,verify_compressed_data_integrity=False)
        # cur_data = torch.from_numpy(cur_sample['csi_data']).to(torch.complex64)
        cur_data = torch.from_numpy(cur_sample['feature']).to(torch.complex64)
        cur_cond = torch.from_numpy(cur_sample['cond']).to(torch.complex64).squeeze(0)
        cur_cond[-2] = calculate_distance(map_positions[int(cur_cond[-2])], tx_position)
        return {
            'data': cur_data,
            'cond': cur_cond
        }


class Collator:
    def __init__(self, params):
        self.params = params

    def collate(self, minibatch):
        sample_rate = self.params.sample_rate
        task_id = self.params.task_id
        ## WiFi Case
        if task_id == 0:
            for record in minibatch:
                # Filter out records that aren't long enough.
                if len(record['data']) < sample_rate:
                    del record['data']
                    del record['cond']
                    continue
                data = torch.view_as_real(record['data']).permute(1, 2, 0)
                down_sample = F.interpolate(data, sample_rate, mode='nearest-exact')
                norm_data = (down_sample - down_sample.mean()) / down_sample.std()
                # norm_data = (norm_data - norm_data.min()) / (norm_data.max() - norm_data.min())
                # norm_data = down_sample
                record['data'] = norm_data.permute(2, 0, 1)
            data = torch.stack([record['data'] for record in minibatch if 'data' in record])
            cond = torch.stack([record['cond'] for record in minibatch if 'cond' in record])
            return {
                'data': data,
                'cond': torch.view_as_real(cond),
            }
        else:
            raise ValueError("Unexpected task_id.")


def from_path(params, is_distributed=False):
    data_dir = params.data_dir
    task_id = params.task_id
    if task_id == 0:
        dataset = WiFiDataset(data_dir)
    else:
        raise ValueError("Unexpected task_id.")
    return torch.utils.data.DataLoader(
        dataset,
        batch_size=params.batch_size,
        collate_fn=Collator(params).collate,
        shuffle=not is_distributed,
        num_workers=8,
        sampler=DistributedSampler(dataset) if is_distributed else None,
        pin_memory=True,
        drop_last=True,
        persistent_workers=True)


def from_path_inference(params):
    cond_dir = params.cond_dir
    task_id = params.task_id
    if task_id == 0:
        dataset = WiFiDataset(cond_dir)
    else:
        raise ValueError("Unexpected task_id.")
    return torch.utils.data.DataLoader(
        dataset,
        batch_size=params.inference_batch_size,
        collate_fn=Collator(params).collate,
        shuffle=False,
        num_workers=os.cpu_count()
        )
