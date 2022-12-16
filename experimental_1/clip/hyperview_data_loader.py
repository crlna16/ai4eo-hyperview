"""
This code is generated by Ridvan Salih KUZU
LAST EDITED:  20.06.2022
ABOUT SCRIPT:
It defines Data Loader class and functions
"""

import torch
from torch.utils.data import Subset
from sklearn.model_selection import train_test_split
from torchvision import transforms
from torch.utils.data import Dataset
import pandas as pd
import glob
import numpy as np
import os
import albumentations as A
from tqdm import tqdm
from albumentations.pytorch import ToTensorV2



class DataReader(Dataset):
    def __init__(self, database_dir, label_paths=None, transform=None,target_index=[0,1,2,3],cropped_load=False):

        self.transform = transform
        if cropped_load:
            self.images = DataReader.load_cropped_data(database_dir)
        else:
            self.images =  DataReader.load_data(database_dir)
        self.target_index=target_index

        if label_paths is not None:
            self.labels = DataReader.load_gt(label_paths)
        else:
            self.labels = np.zeros([len(self.images),4])

    def __len__(self):
        return len(self.images)

    def __getitem__(self, index):

        img = self.images[index]
        lab = self.labels[index][self.target_index]
        if self.transform is not None:
            img = self.transform(image=img.astype(np.float32))["image"]
        else:
            img = img.transpose((2, 0, 1))
            img = torch.from_numpy(img.astype(np.float32))

        return img, torch.from_numpy(lab.astype(np.float32))

    @staticmethod
    def load_gt(file_path: str):
        """Load labels for train set from the ground truth file.
        Args:
            file_path (str): Path to the ground truth .csv file.
        Returns:
            [type]: 2D numpy array with soil properties levels
        """
        gt_file = pd.read_csv(file_path)
        labels = gt_file[["P", "K", "Mg", "pH"]].values / np.array([325.0, 625.0, 400.0, 7.8])  # normalize ground-truth between 0-1
        return labels

    @staticmethod
    def load_data(directory: str):
        """Load each cube, reduce its dimensionality and append to array.

        Args:
            directory (str): Directory to either train or test set
        Returns:
            [type]: A list with spectral curve for each sample.
        """
        image_list = []

        all_files = np.array(
            sorted(
                glob.glob(os.path.join(directory, "*.npz")),
                key=lambda x: int(os.path.basename(x).replace(".npz", "")),
            )
        )
        for file_name in tqdm(all_files, total=len(all_files),desc="INFO: Data preloading ..."):
            with np.load(file_name) as npz:
                mask = npz['mask']
                data = npz['data']

                m = (1 - mask.astype(int))
                image = (data * m)
                image = DataReader._shape_pad(image)
                image = image.transpose((1, 2, 0))
                image = image/5419

                image_list.append(image)
                #if len(image_list)==10: break

        return image_list


    @staticmethod
    def load_cropped_data(directory:str):
        image_list = []

        all_files = np.array(
            sorted(
                glob.glob(os.path.join(directory, "*.npz")),
                key=lambda x: int(os.path.basename(x).replace(".npz", "")),
            )
        )
        for idx, file_name in tqdm(enumerate(all_files), total=len(all_files),
                                   desc="INFO: Data preloading for cropped augmentation.."):
            # print(file_name)
            with np.load(file_name) as npz:
                flag = True
                mask = npz["mask"]
                data = npz["data"]
                ma = np.max(data, keepdims=True)
                sh = data.shape[1:]
                for i in range(10):
                    # Repeating 11x11 cropping 10 times does not mean we use all croppings:
                    # as seen in the Flag=False below at the end of the loop,
                    # when we reach at the good crop (not coinciding to the masked area) we stop searching

                    edge = 11  # Randomly cropping the fields with 11x11 size, and adding some noise to the cropped samples
                    x = np.random.randint(sh[0] + 1 - edge)
                    y = np.random.randint(sh[1] + 1 - edge)
                    if np.sum(mask[0, x: (x + edge),
                              y: (y + edge)]) > 120:  # get crops having meaningful pixels, not zeros
                        aug_data = (
                                data[:, x: (x + edge), y: (y + edge)]
                                + np.random.uniform(-0.01, 0.01, (150, edge, edge)) * ma
                        )
                        aug_mask = mask[
                                   :, x: (x + edge), y: (y + edge)
                                   ] | np.random.randint(0, 1, (150, edge, edge))

                        flag = False  # break the loop when you have a meaningful crop
                        break

                if flag:  # After having  11x11 croped sample, get another crop considering the minimum edge length: (min_edge,min_edge)
                    max_edge = np.max(sh)
                    min_edge = np.min(sh)  # AUGMENT BY SHAPE
                    edge = min_edge  # np.random.randint(16, min_edge)
                    x = np.random.randint(sh[0] + 1 - edge)
                    y = np.random.randint(sh[1] + 1 - edge)
                    aug_data = (
                            data[:, x: (x + edge), y: (y + edge)]
                            + np.random.uniform(-0.001, 0.001, (150, edge, edge)) * ma
                    )
                    aug_mask = mask[
                               :, x: (x + edge), y: (y + edge)
                               ] | np.random.randint(0, 1, (150, edge, edge))

                aug_data = aug_data.transpose((1, 2, 0))
                aug_data = aug_data / 5419
                image_list.append(aug_data)

        return image_list


    @staticmethod
    def _shape_pad(data):
        max_edge = np.max(data.shape[1:])
        shape = (max_edge, max_edge)
        padded = np.pad(data, ((0, 0),
                               (0, (shape[0] - data.shape[1])),
                               (0, (shape[1] - data.shape[2]))),
                        'wrap')
        # print(padded.shape)
        return padded




class HyperDataloader():
    """
    THIS CLASS ORCHESTRATES THE TRAINING, VALIDATION, AND TEST DATA GENERATORS
    """
    def __init__(self,
                 train_root,
                 label_path,
                 test_root,
                 im_size=224,
                 split=0.20,
                 batch_size=2,
                 num_workers=1,
                 target_index=None):


        trans_tr, trans_te = HyperDataloader._init_transform(im_size)

        train_dataset = DataReader(train_root, label_path, transform=trans_tr, target_index=target_index)
        tr_dataset, val_dataset = HyperDataloader.train_val_dataset(train_dataset, split)
        test_dataset = DataReader(test_root, transform=trans_te,target_index=target_index)

        self.dataloaders = {}

        self.dataloaders['train'] = torch.utils.data.DataLoader(tr_dataset,batch_size=batch_size,
                                                           shuffle=True, num_workers=num_workers, pin_memory=True)
        self.dataloaders['valid'] = torch.utils.data.DataLoader(val_dataset, batch_size=batch_size,
                                                           shuffle=True, num_workers=num_workers, pin_memory=True)
        self.dataloaders['test'] = torch.utils.data.DataLoader(test_dataset, batch_size=batch_size,
                                                           shuffle=False, num_workers=num_workers, pin_memory=False)

    def get_data_loader(self, type):
        return self.dataloaders[type]

    @staticmethod
    def _init_transform(image_shape):
        train_transform = A.Compose([
            A.Resize(image_shape, image_shape),

            A.GaussNoise(var_limit=0.000025, p=0.5),
            A.ElasticTransform(p=0.25),
            A.RandomRotate90(p=0.5),
            # A.Rotate(),
            A.RandomResizedCrop(image_shape, image_shape, ratio=(0.95, 1.05), p=0.5),
            A.Flip(p=0.5),
            A.ShiftScaleRotate(rotate_limit=90, shift_limit_x=0.05, shift_limit_y=0.05, p=0.5),
            # A.RandomBrightnessContrast(),
            ToTensorV2(),

        ])

        eval_transform = A.Compose([
            A.Resize(image_shape, image_shape),
            # A.Normalize(mean=eval_stats[0]*eval_stats[2], std=eval_stats[1]*eval_stats[2], max_pixel_value=1)
            ToTensorV2(),
        ])

        return train_transform, eval_transform



    @staticmethod
    def train_val_dataset(dataset, val_split=0.20):
        train_idx, val_idx = train_test_split(list(range(len(dataset))), test_size=val_split)
        train_data = Subset(dataset, train_idx)
        valid_data = Subset(dataset, val_idx)
        return train_data,valid_data