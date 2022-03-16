#! /usr/bin/env python3

import os
try:
    import nni
except ImportError:
    pass
import copy
import time
import argparse
import numpy as np
import datetime
from collections import defaultdict
import pprint
from glob import glob
import pandas as pd

import torch
import pytorch_lightning as pl
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset, Subset, random_split

from pytorch_lightning.callbacks.early_stopping import EarlyStopping
from pytorch_lightning.callbacks import ModelCheckpoint, Callback
from pytorch_lightning.callbacks.model_summary import ModelSummary

from typing import Optional

from sklearn.metrics import mean_squared_error
from sklearn.model_selection import KFold

from mod_pse import PixelSetEncoder
from mod_ltae import LTAE

import mod_utils

class HyperviewDataModule(mod_utils.BaseKFoldDataModule):
    """ Lightning data module for Hyperview data """

    def __init__(self, args):
        super().__init__()
        self.args = args

    def prepare_data(self):
        '''Use this method to do things that might write to disk or that need to be done 
        only from a single process in distributed settings.'''
        pass

    def setup(self, stage: Optional[str] = None):
        '''
        Data operations performed on every GPU

        setup() expects an stage: Optional[str] argument. It is used to separate setup logic 
        for trainer.{fit,validate,test}. If setup is called with stage = None, we assume all 
        stages have been set-up.

        Creates self.{train_data, valid_data, test_data} depending on 'stage' (HyperviewDataset)
        '''

        if stage in (None, 'fit'):
            dataset = HyperviewDataset('train', self.args)

            # create a holdout dataset
            self.train_data, self.holdout_data = random_split(dataset, [1600, 132])

            self.input_shapes = dataset.X.shape[1:]
        if stage in (None, 'test'):
            self.test_data = HyperviewDataset('test', self.args)

    def train_dataloader(self):
        return DataLoader(self.train_fold, batch_size=self.args.batch_size, 
                          num_workers=self.args.num_workers, shuffle=True, drop_last=True)

    def val_dataloader(self):
        return DataLoader(self.valid_fold, batch_size=self.args.test_batch_size, 
                          num_workers=self.args.num_workers, shuffle=False, drop_last=False)

    def test_dataloader(self):
        return DataLoader(self.holdout_data, batch_size=self.args.test_batch_size, 
                          num_workers=self.args.num_workers, shuffle=False, drop_last=False)

    def predict_dataloader(self, args): # predicts on test set
        return DataLoader(self.test_data, batch_size=self.args.test_batch_size, 
                          num_workers=self.args.num_workers, shuffle=False, drop_last=False)

    def setup_folds(self, num_folds: int) -> None:
        self.num_folds = num_folds
        self.splits = [split for split in KFold(num_folds).split(range(len(self.train_data)))]

    def setup_fold_index(self, fold_index: int) -> None:
        train_indices, valid_indices = self.splits[fold_index]
        self.train_fold = Subset(self.train_data, train_indices)
        self.valid_fold = Subset(self.train_data, valid_indices)

    @staticmethod
    def add_dataloader_specific_args(parent_parser):
        parser = argparse.ArgumentParser(parents=[parent_parser], add_help=False)
        parser.add_argument('--num-workers', type=int, default=1, help='dataloader processes')
        return parser

class HyperviewDataset(Dataset):
    """ Handles everything all Datasets of the different Model have in common like loading the same data files."""
    def __init__(self, flag, args):
        '''
        Load data and apply transforms during setup

        Parameters:
        -----------
        flag : string
            Any of train / valid / test. Defines dataset.
        args : argparse Namespace
            arguments passed to the main script
        -----------
        Returns: dataset
        '''
        self.args=args

        self.load_data(flag)

        print(f'Loaded dataset {flag}')
        print(f'Samples: {self.X.shape}')


        if flag == 'train':
            self.load_gt()

            print(f'Labels: {self.y.shape} with regression targets: {args.selected_targets}')
        elif flag == 'test':
            self.y = np.zeros([len(self.X), len(self.args.selected_targets)]) # dummy target
            self.baseline = np.zeros(len(self.args.selected_targets))
        
    def load_data(self, flag):
        """Load each cube, reduce its dimensionality and append to array.
    
        Args:
            flag (str): subdirectory
        Assigns self.X:
            [type]: A np array with spectral curve for each sample.
        """
        data = []
        if self.args.data_processing == 'spectral-curve':
            filtering = mod_utils.SpectralCurveFiltering()
        elif self.args.data_processing == 'random-selection':
            filtering = mod_utils.RandomPixelSelector(self.args.n_pixels)
        raw_data_root = os.path.join(self.args.data, f'{flag}_data')
        if flag == 'train':
            raw_data_root = os.path.join(raw_data_root, 'train_data')

        print('\n' + '*'*40)
        print('Loading data from', raw_data_root)
        start_time = time.time()
        all_files = np.array(
            sorted(
                glob(os.path.join(raw_data_root, "*.npz")),
                key=lambda x: int(os.path.basename(x).replace(".npz", "")),
            )
        )
        for file_name in all_files:
            with np.load(file_name) as npz:
                arr = np.ma.MaskedArray(**npz)
            arr = filtering(arr)
            data.append(arr)

        data = np.array(data) / self.args.c_norm
        print(f'Maximum value after normalization: {np.max(data)}')
        self.X = torch.tensor(data, dtype=torch.float32)
        print(f'Finished loading data in {time.time() - start_time:.0f} seconds')
        print('*'*40 + '\n')
    
    def load_gt(self):
        """
            Load labels for train set from the ground truth file.

            Keep only selected targets

        Assigns y:
            [type]: 2D numpy array with soil properties levels
        """
        file_path = os.path.join(self.args.data, "train_data", "train_gt.csv")
        gt_file = pd.read_csv(file_path)
        labels = gt_file[self.args.selected_targets].values
        # get baseline values
        baseline = np.mean(labels, axis=0)
        for i,sv in enumerate(self.args.selected_targets):
            print(f'Baseline for {sv:2s}: {baseline[i]:.2f}')
        self.baseline = baseline
        self.y = torch.tensor(labels.reshape(len(labels), len(self.args.selected_targets)), dtype=torch.float32)
        
    def __len__(self):
        return self.X.shape[0]

    def __getitem__(self, idx):
        X = self.X[idx]
        y = self.y[idx]
        bl = self.baseline
        return (X, y, bl)

class DenseNet(pl.LightningModule):
    def __init__(self, args, input_shapes):
        super().__init__()
        self.args = args
        # model set up goes here
        self.activation = HyperviewNet.activation_fn(self.args.activation)
        self.loss = HyperviewNet.loss_fn(args.loss)

        self.fc1 = torch.nn.Linear(input_shapes, self.args.units_dense1)
        self.dr_fc1 = torch.nn.Dropout(self.args.dropout_dense1)
        self.fc2 = torch.nn.Linear(self.args.units_dense1, self.args.units_dense2)
        self.dr_fc2 = torch.nn.Dropout(self.args.dropout_dense2)
        self.fc_final = torch.nn.Linear(self.args.units_dense2, len(args.selected_targets))

    def forward(self, x):
        x = self.dr_fc1(self.activation(self.fc1(x)))
        x = self.dr_fc2(self.activation(self.fc2(x)))
        x = self.fc_final(x)
        return x

    @staticmethod
    def add_model_specific_args(parent_parser):
        parser = argparse.ArgumentParser(parents=[parent_parser], add_help=False)
        parser.add_argument('--units-dense1', type=int, default=64)
        parser.add_argument('--units-dense2', type=int, default=64)
        parser.add_argument('--dropout-dense1', type=float, default=0.0)
        parser.add_argument('--dropout-dense2', type=float, default=0.0)
        return parser

class PseLTaeNet(pl.LightningModule):
    def __init__(self, args, input_shapes):
        super().__init__()
        self.args = args
        self.activation = HyperviewNet.activation_fn(self.args.activation)
        self.loss = HyperviewNet.loss_fn(args.loss)

        self.spatial_encoder = PixelSetEncoder(1, # TODO check -- only 1 "channel" hereinput_shapes, 
                                               mlp1=args.mlp1, 
                                               pooling=args.pooling, 
                                               mlp2=args.mlp2, 
                                               with_extra=args.with_extra,
                                               extra_size=args.extra_size)

        self.temporal_encoder = LTAE(in_channels=args.mlp2[-1], 
                                     n_head=args.n_head, 
                                     d_k=args.d_k,
                                     d_model=args.d_model, 
                                     n_neurons=args.mlp3, 
                                     dropout=args.
                                     dropout,
                                     T=args.T, 
                                     len_max_seq=args.len_max_seq, 
                                     positions=args.positions, 
                                     return_att=args.return_att)

        self.decoder = self.get_decoder()
        self.param_ratio()

    def get_decoder(self):
        """
        FROM original PseLTae Github

        Returns an MLP with the layer widths specified in n_neurons.
        Every linear layer but the last one is followed by BatchNorm + ReLu

        The last layer output dimension matches the number of selected regression targets

        args:
            n_neurons (list): List of int that specifies the width and length of the MLP.
        """
        layers = []
        self.args.mlp4 = self.args.mlp4 + [len(self.args.selected_targets)]
        for i in range(len(self.args.mlp4)-1):
            layers.append(nn.Linear(self.args.mlp4[i], self.args.mlp4[i+1]))
            if i < (len(self.args.mlp4) - 2):
                layers.extend([
                    nn.BatchNorm1d(self.args.mlp4[i + 1]),
                    nn.ReLU()
                ])
        m = nn.Sequential(*layers)
        return m


    def param_ratio(self):
        """
        FROM original PseLTae Github
        """
        def get_ntrainparams(model):
            return sum(p.numel() for p in model.parameters() if p.requires_grad)

        total = get_ntrainparams(self)
        s = get_ntrainparams(self.spatial_encoder)
        t = get_ntrainparams(self.temporal_encoder)
        c = get_ntrainparams(self.decoder)

        print('TOTAL TRAINABLE PARAMETERS : {}'.format(total))
        print('RATIOS: Spatial {:5.1f}% , Temporal {:5.1f}% , Classifier {:5.1f}%'.format(s / total * 100,
                                                                                          t / total * 100,
                                                                                          c / total * 100))
        return total

    def forward(self, x):
        """
         Args:
            input(tuple): (Pixel-Set, Pixel-Mask) or ((Pixel-Set, Pixel-Mask), Extra-features)
            Pixel-Set : Batch_size x Sequence length x Number of pixels
            Extra-features : Batch_size x Sequence length x Number of features
        """
        out = self.spatial_encoder(x) 

        if self.args.return_att:
            out, att = self.temporal_encoder(out)
            out = self.decoder(out)
            return out, att
        else:
            out = self.temporal_encoder(out)
            out = self.decoder(out)
            return out

    @staticmethod
    def add_model_specific_args(parent_parser):
        parser = argparse.ArgumentParser(parents=[parent_parser], add_help=False)
        parser.add_argument('--input-dim', type=int, default=10)
        parser.add_argument('--mlp1', type=int, nargs=3, default=[1, 32, 64])
        parser.add_argument('--mlp2', type=int, nargs=2, default=[128, 128])
        parser.add_argument('--mlp3', type=int, nargs=2, default=[256, 128])
        parser.add_argument('--mlp4', type=int, nargs=3, default=[128, 64, 32],)
        parser.add_argument('--pooling', type=str, default='mean_std')
        parser.add_argument('--with-extra', action='store_true')
        parser.add_argument('--extra-size', type=int, default=4,)
        parser.add_argument('--n-head', type=int, default=16)
        parser.add_argument('--d-k', type=int, default=8)
        parser.add_argument('--d-model', type=int, default=256)
        parser.add_argument('--dropout', type=int, default=0.2)
        parser.add_argument('--T', type=int, default=1000)
        parser.add_argument('--len-max-seq', type=int, default=150)
        parser.add_argument('--positions', type=int, default=None)
        parser.add_argument('--return-att', action='store_true')
        # releated to pseltae data
        parser.add_argument('--n-pixels', type=int, default=32, help='Random pixels to select if --data-processing = random-selection')
        return parser

class HyperviewNet(pl.LightningModule):
    def __init__(self, backbone):
        super().__init__()
        self.backbone = backbone
        self.save_hyperparameters(self.backbone.args)
        self.best_loss = np.inf # reported for nni
        self.best_epoch = 0

    def forward(self, x):
        y = self.backbone(x)
        return y

    def training_step(self, batch, batch_idx):
        x, y, _ = batch
        y_pred = self.backbone(x)
        #y_pred = torch.squeeze(y_pred, dim=1)
        loss = self.backbone.loss(y_pred, y)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y, bl = batch
        y_pred = self.backbone(x)
        #y_pred = torch.squeeze(y_pred, dim=1)
        loss = self.backbone.loss(y_pred, y)

        
        score = 0
        for i in range(len(y[0])):
            loss_baseline = self.backbone.loss(bl[:,i], y[:,i])
            loss_model    = self.backbone.loss(y_pred[:,i], y[:,i])
            # print(i, loss_model, loss_baseline, loss_model/loss_baseline)
            score += loss_model / loss_baseline
        score /= (i+1)

        self.log('valid_loss', loss, on_epoch=True)
        self.log('baseline_score', score, on_epoch=True)
        return loss

    def validation_epoch_end(self, outputs):
        val_loss = self.trainer.callback_metrics["valid_loss"]
        if val_loss < self.best_loss:
            self.best_loss = val_loss
            self.best_epoch = self.trainer.current_epoch
        self.log('best_loss', self.best_loss)
        self.log('best_epoch', self.best_epoch)

    def test_step(self, batch, batch_idx):
        x, y, _ = batch
        y_pred = self.backbone(x)
        #y_pred = torch.squeeze(y_pred, dim=1)
        loss = self.backbone.loss(y_pred, y)
        self.log('test_loss', loss)

    def predict_step(self, batch, batch_idx):
        x, y, _ = batch
        y_pred = self.backbone(x)
        #y_pred = torch.squeeze(y_pred, dim=1)
        return y_pred

    def configure_optimizers(self):
        if self.backbone.args.optimizer=='adam':
            return torch.optim.Adam(self.parameters(), lr=self.hparams.learning_rate)
        elif self.backbone.args.optimizer=='sgd':
            return torch.optim.SGD(self.parameters(), lr=self.hparams.learning_rate)

    def configure_callbacks(self):
        '''Model checkpoint callback goes here'''
        callbacks = [ModelCheckpoint(monitor='valid_loss', mode='min',
                     dirpath=os.path.join(os.path.dirname(self.backbone.args.save_model_path), 'checkpoint'),
                     filename="hyperviewnet-{epoch}")]
        return callbacks

    @staticmethod
    def activation_fn(activation_name):
        if activation_name == 'tanh':
            return torch.tanh
        elif activation_name == 'relu':
            return F.relu
        elif activation_name == 'sigmoid':
            return torch.sigmoid
        elif activation_name == 'leaky_relu':
            return F.leaky_relu

    @staticmethod
    def loss_fn(loss_name):
        if loss_name == 'mse':
            return F.mse_loss
        elif loss_name == 'mae':
            return F.l1_loss
        elif loss_name == 'rel-mse':
            # loss relative to baseline
            return mod_utils.relative_mse

    @staticmethod
    def best_checkpoint_path(save_model_path, best_epoch):
        '''Path to best checkpoint'''
        ckpt_path = os.path.join(os.path.dirname(save_model_path), 'checkpoint', f"hyperviewnet-epoch={best_epoch}.ckpt")
        all_ckpts = os.listdir(os.path.dirname(ckpt_path))
        # If the checkpoint already exists, lightning creates "*-v1.ckpt"
        only_ckpt = ~np.any([f'-v{best_epoch}' in ckpt for ckpt in all_ckpts])
        assert only_ckpt, f'Cannot load checkpoint: found versioned checkpoints for best_epoch {best_epoch} in {os.path.dirname(ckpt_path)}'
        return ckpt_path

class HyperviewMetricCallbacks(Callback):
    def __init__(self, args):
        super().__init__()
        self.args = args

    def on_validation_epoch_end(self, trainer, pl_module):
        '''After each epoch metrics on validation set'''
        if self.args.nni:
            nni.report_intermediate_result(float(trainer.callback_metrics['valid_loss']))
        metrics = trainer.callback_metrics # everything that was logged in self.log
        epoch = trainer.current_epoch
        print(f'Epoch {epoch} metrics:')
        for key, item in metrics.items():
            print(f'  {key}: {item:.4f}')

    def on_train_epoch_start(self, trainer, pl_module):
        print(f'\nEpoch {trainer.current_epoch} starts training ...')
        self.epoch_start_time = time.time()

    def on_train_epoch_end(self, trainer, pl_module):
        tt = time.time() - self.epoch_start_time
        print(f'Epoch {trainer.current_epoch} finished training in {tt:.0f} seconds')

    def on_test_epoch_end(self, trainer, pl_module):
        pass

    def on_epoch_end(self, trainer, pl_module):
        '''After each epoch (T+V)'''
        pass

    def on_train_end(self, trainer, pl_module):
        '''Final metrics on validation set (after training is done)'''
        print(f'Finished training in {trainer.current_epoch+1} epochs')
        if self.args.nni:
            nni.report_final_result(float(trainer.callback_metrics['best_loss']))

    @staticmethod
    def add_nni_params(args):
        args_nni = nni.get_next_parameter()
        assert all([key in args for key in args_nni.keys()]), 'need only valid parameters'
        args_dict = vars(args)
        # cast params that should be int to int if needed (nni may offer them as float)
        args_nni_casted = {key:(int(value) if type(args_dict[key]) is int else value)
                            for key,value in args_nni.items()}
        args_dict.update(args_nni_casted)

        # adjust paths to NNI_OUTPUT_DIR (overrides passed args)
        nni_output_dir = os.path.expandvars('$NNI_OUTPUT_DIR')
        for param in ['save_model_path', 'prediction_output_path']:
            nni_path = os.path.join(nni_output_dir, os.path.basename(args_dict[param]))
            args_dict[param] = nni_path
        return args

def main():
    # ----------
    # args
    # ----------

    np.random.seed(404)
    
    parser = argparse.ArgumentParser()
    # hyperparameters
    parser.add_argument('--model', type=str, choices=['dense', 'pseltae'], default='dense',
                         help='''Model architecture. 
                                 dense - DenseNet, simple feed forward NN''')
    parser.add_argument('--learning-rate', type=float, default=1e-3)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--test-batch-size', type=int, default=256, help='Larger batch size for validation data')
    parser.add_argument('--optimizer', type=str, default='adam', choices=['adam', 'sgd'])
    parser.add_argument('--loss', type=str, default='mse', choices=['mse', 'mae', 'rel-mse'])
    parser.add_argument('--activation', type=str, default='relu', choices=['relu'])
    # data
    parser.add_argument('--data', type=str, help='should enlist train_data.h5, valid_data.h5, (test_data.h5)')
    parser.add_argument('--selected-targets', type=str, nargs='+', choices=['P', 'K', 'Mg', 'pH'], default=['P', 'K', 'Mg', 'pH'], help='Selected regression targets')
    parser.add_argument('--data-processing', type=str, choices=['random-selection', 'spectral-curve'], default='random-selection')
    parser.add_argument('--c-norm', type=float, default=2000, help='Normalization constant')
    # training
    parser.add_argument('--early-stopping', dest='early_stopping', action='store_true')
    parser.add_argument('--no-early-stopping', dest='early_stopping', action='store_false')
    parser.set_defaults(early_stopping=True)
    parser.add_argument('--patience', type=int, default=3, 
                         help='Epochs to wait before early stopping')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--nni', action='store_true')
    parser.add_argument('--k-fold', type=int, help='Folds for K-fold cross validation', default=5)
    # store and load
    parser.add_argument('--save-model-path', type=str, default='./best_model.pt')
    parser.add_argument('--prediction-output-path', type=str, default='best_predictions.h5')
    parser.add_argument('--load-model-path', type=str, default='')

    parser = pl.Trainer.add_argparse_args(parser)
    parser = HyperviewDataModule.add_dataloader_specific_args(parser)

    # add model specific args depending on chosen model
    temp_args, _ = parser.parse_known_args()
    if temp_args.model=='dense':
        parser = DenseNet.add_model_specific_args(parser)
    elif temp_args.model=='pseltae':
        parser = PseLTaeNet.add_model_specific_args(parser)

    args = parser.parse_args()

    if args.nni:
        args = HyperviewMetricCallbacks.add_nni_params(args)

    if args.verbose:
        print('BEGIN argparse key - value pairs')
        pp = pprint.PrettyPrinter(indent=4)
        pp.pprint(vars(args))
        print('END argparse key - value pairs')

    if args.load_model_path:
        print('INFERENCE MODE')
        print(f'loading model from {args.load_model_path}')

        # ----------
        # data
        # ----------

        # load arg Namespace from checkpoint
        print('command line arguments will be replaced with checkpoint["hyper_parameters"]')
        checkpoint = torch.load(args.load_model_path)
        checkpoint_args = argparse.Namespace(**checkpoint["hyper_parameters"])

        # potentially overwrite the data arg
        if args.data:
            checkpoint_args.data = args.data
            print(f'overwriting checkpoint argument: data dir = {checkpoint_args.data}')

        cdm = HyperviewDataModule(checkpoint_args)
        cdm.setup(stage='test')
        test_loader = cdm.predict_dataloader()
        
        if args.verbose:
            print('Input shapes', cdm.input_shapes)

        if checkpoint_args.model=='dense':
            backbone = DenseNet(checkpoint_args, cdm.input_shapes)
        elif checkpoint_args.model=='pseltae':
            backbone = PseLTaeNet(checkpoint_args, cdm.input_shapes)
        # load model state from checkpoint
        model = HyperviewNet(backbone)
        model.load_state_dict(checkpoint['state_dict'])
        model.eval()

        trainer = pl.Trainer(weights_summary='full', 
                             num_sanity_val_steps=0, 
                             enable_progress_bar=False)
        trainer.test(model=model, test_dataloaders=test_loader)
        y_pred = trainer.predict(model=model, dataloaders=[test_loader])
        y_pred = torch.cat(y_pred).detach().cpu().numpy().squeeze()
        
    else:
        print('TRAINING MODE')

        # ----------
        # data
        # ----------

        cdm = HyperviewDataModule(args)
        cdm.setup(stage='fit')

        #train_loader = cdm.train_dataloader()
        #valid_loader = cdm.val_dataloader()

        if args.verbose:
            print('Input shapes', cdm.input_shapes)
        # ----------
        # model
        # ----------
        if args.model=='dense':
            model = HyperviewNet(DenseNet(args, cdm.input_shapes))
        elif args.model=='pseltae':
            model = HyperviewNet(PseLTaeNet(args, cdm.input_shapes))

        # ----------
        # training
        # ----------
        callbacks = [HyperviewMetricCallbacks(args), ModelSummary(max_depth=-1)] # model checkpoint is a model callback
        if args.early_stopping:
            callbacks.append(EarlyStopping(monitor='valid_loss', patience=args.patience, mode='min'))

        trainer = pl.Trainer.from_argparse_args(args, 
                                                fast_dev_run=False, # debug option
                                                logger=False,
                                                callbacks=callbacks, 
                                                enable_progress_bar=False,
                                                num_sanity_val_steps=0) # skip validation check

        internal_fit_loop = trainer.fit_loop
        trainer.fit_loop = mod_utils.KFoldLoop(args.k_fold, export_path="./k_fold/")
        trainer.fit_loop.connect(internal_fit_loop)
        trainer.fit(model, cdm)

	# TODO this goes inside the cross validation
        best_epoch = int(trainer.callback_metrics["best_epoch"])
        ckpt_path = HyperviewNet.best_checkpoint_path(args.save_model_path, best_epoch)
        print(f'\nLoading best model from {ckpt_path}')
        trainer.validate(dataloaders=valid_loader, ckpt_path=ckpt_path)
        #model.load_state_dict(torch.load(ckpt_path)['state_dict'])
        model.eval()
        # make predictions on *validation set*
        y_pred = trainer.predict(model=model, dataloaders=[valid_loader])
        y_pred = torch.cat(y_pred).detach().cpu().numpy().squeeze()

        y_baseline = np.tile(cdm.train_data.dataset.baseline, len(y_pred)).reshape(len(y_pred), -1)
        
        y_true = cdm.valid_data.dataset.y[1300:]

        score = 0
        
        for i, sv in enumerate(args.selected_targets):
            mse_model = mean_squared_error(y_true[:,i], y_pred[:,i])
            mse_baseline = mean_squared_error(y_true[:,i], y_baseline[:,i])

            score += mse_model / mse_baseline

            print('*'*40)
            print('   ', sv)
            print(f'MSE (model): {mse_model:.2f}, MSE (baseline): {mse_baseline:.2f}')
            print(f'Relative score: {100 * (mse_model - mse_baseline) / mse_baseline:.2f} %')
            print('*'*40)

        score /= len(args.selected_targets)

        print(f'SCORE: {score:.4f}')

    # procedures that take place in fit and in test stage
    # save predictions
    # TODO save predictions y_pred in the required json format

if __name__=='__main__':
    main()