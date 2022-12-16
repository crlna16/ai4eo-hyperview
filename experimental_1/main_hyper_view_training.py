"""
This code is generated by Ridvan Salih KUZU
LAST EDITED:  20.06.2022
ABOUT SCRIPT:
It runs few shot learning on given CLIP models and makes hyperparameter fine-tuning
"""

import csv
import clip
from clip.hyperview_data_loader import HyperDataloader
from clip.losses import CustomMSE
from clip.utils import AvgMeter, get_lr
from clip.downstream_task import TaskType
import copy
import torch
from tqdm import tqdm
import optuna
from optuna.samplers import TPESampler
import argparse
import warnings
import pandas as pd
import joblib
import gc
import os
import numpy as np

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)


def main(args):

    if not os.path.exists(args.log_dir):
        os.makedirs(args.log_dir)


    available_models = clip.available_models()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    selected_model_name = available_models[-2] #ViT-L/14
    class_number=len(args.target_index)

    model, default_transform = clip.load(selected_model_name, device, downstream_task=TaskType.HYPERVIEW, class_num=class_number,download_root=args.log_dir)
    data_loader = HyperDataloader(args.train_dir, args.label_dir, args.eval_dir, model.visual.input_resolution, 0.25, batch_size=args.batch_size, num_workers=args.num_workers, target_index=args.target_index)

    def objective(trial):
        '''
            THIS FUNCTION DEFINES THE OBJECTIVE FUNCTION FOR BAYESIAN HYPERPARAMETER TUNING
            :param trial: trial object of bayesian optimization
            :return: returns weighted F1 score to be maximized
        '''

        gc.collect()

        print(f"INFO: Trial number: {trial.number}\n")

        learning_rate = trial.suggest_categorical('learning_rate', args.learning_rate)
        penalty_rate = trial.suggest_categorical('learning_rate', args.penalty_rate)
        trainable_layers = trial.suggest_categorical('trainable_layers', args.trainable_layers)

        output_file_template = '{}_{}_{}_{}_{}_{}'.format(args.log_dir,learning_rate,penalty_rate,trainable_layers,args.target_index, trial.number)

        model, default_transform = clip.load(selected_model_name, device, downstream_task=TaskType.HYPERVIEW, class_num=class_number,download_root=args.log_dir)

        if 'ViT' in selected_model_name:
            print('INFO: training model name: {}'.format(selected_model_name))
            set_vit_trainable_layers(model, trainable_layers, TaskType.HYPERVIEW)
        else:
            print('')
            #raise NotImplementedError
            # since ViT models perform better in given linear probe evaluation in '01_b_clip_linear_probe_tuning.py'
            # we skip the implementation for Resnet based models

        #if torch.cuda.device_count() > 1:
        #    print('INFO: Multiple GPU was found: {}'.format(torch.cuda.device_count()))
        #    model = torch.nn.DataParallel(model)
        #    model = model.to(device)

        best_model=train_validate(model, data_loader.get_data_loader('train'), data_loader.get_data_loader('valid'), learning_rate,penalty_rate, output_file_template, device, args.target_index)

        #model, transform = clip.load('{}_model_best.pth.tar'.format(output_file_template),device,downstream_task=TaskType.HYPERVIEW,class_num=4)

        y_true, y_pred = inference_mlp_head(best_model, data_loader.get_data_loader('valid'), device)

        monitored_score = inference_reporting(y_true, y_pred, output_file_template,args.target_index)

        y_true, y_pred = inference_mlp_head(best_model, data_loader.get_data_loader('test'), device)
        prepare_submission(y_pred, output_file_template,args.target_index)

        return monitored_score

    study = optuna.create_study(sampler=TPESampler(), direction='minimize',
                                pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=24, interval_steps=6),)

    log_file=args.log_dir+'optimization_logs__{}.pkl'.format(args.target_index)
    if os.path.isfile(log_file):
        study = joblib.load(log_file)

    study.optimize(objective, n_trials=args.n_trials, gc_after_trial=True)
    joblib.dump(study, log_file)


def train_validate(model, train_loader, valid_loader, learning_rate,penalty_rate, output_template, device,target_index):
    '''
        THIS FUNCTION MANAGES THE EXECUTION OF TRAINING AND VALIDATION EPOCHS
        :param model: CLIP model to be fine-tuned
        :param train_loader: data loader for training files
        :param valid_loader: data loader for validation files
        :param learning_rate: learning rate for optimization
        :param downstream_task: one of few-shot learning tasks 'DEFAULT', 'ARC_HEAD','MLP_HEAD','ARC_MLP_HEAD'
        :param output_template: file name template to save the fine-tuned models and inference statistics
        :param device: GPU or CPU device
        :return:
    '''

    best_model=None
    optimizer = torch.optim.AdamW(filter(lambda p: p.requires_grad, model.parameters()), betas=(0.9,0.98),eps=1e-6, lr=learning_rate,weight_decay=penalty_rate)  # weight_decay=0.0001
    lr_scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)

    loss_criterion = CustomMSE(device,target_index).to(device)

    step = "epoch"
    best_loss = float('inf')
    for epoch in range(args.total_epoch):
        print(f"Epoch: {epoch + 1}")
        model.train()

        train_epoch(model, train_loader, optimizer, lr_scheduler, loss_criterion, step, device,target_index)
        model.eval()
        with torch.no_grad():
            valid_loss, image_batch = valid_epoch(model, valid_loader, loss_criterion, device,target_index)

        if valid_loss.avg < best_loss:
            best_loss = valid_loss.avg
            best_model = copy.deepcopy(model)
            torch.jit.save(torch.jit.trace(model, (image_batch)),'{}_model_best.pth.tar'.format(output_template))
            # torch.save(model.state_dict(), "model_best.pth.tar")
            print("INFO: Best Model saved into " + '{}_model_best.pth.tar'.format(output_template))

        lr_scheduler.step(valid_loss.avg)
    return best_model


def train_epoch(model, train_loader, optimizer, lr_scheduler, loss_criterion, step, device,target_index):
    '''
         THIS FUNCTION RUNS THE SINGLE TRAINING EPOCHS
         :param model: CLIP model to be fine-tuned
         :param train_loader: data loader for training files
         :param optimizer: model optimizer object
         :param lr_scheduler: scheduler object to update the learning rate for optimization
         :param loss_criterion: loss objective function to be utilized for backprop
         :param step: epoch step number
         :param device: GPU or CPU device
         :return: returns average training loss
    '''

    loss_meter = AvgMeter()
    tqdm_object = tqdm(train_loader, total=len(train_loader))
    for image_batch, label_batch in tqdm_object:
        output = model(image_batch.to(device), None, label_batch.to(device))
        loss = loss_criterion(output,label_batch.to(device))
        optimizer.zero_grad()
        loss[0].backward()
        optimizer.step()
        if step == "batch":
            lr_scheduler.step()

        count = image_batch.size(0)
        loss_meter.update(loss[0].item(), count)

        tqdm_object.set_postfix(train_loss=loss_meter.avg, lr=get_lr(optimizer))
    return loss_meter


def valid_epoch(model, valid_loader, loss_criterion, device, target_index):
    '''
         THIS FUNCTION RUNS THE SINGLE TRAINING EPOCHS
         :param model: CLIP model to be fine-tuned
         :param valid_loader: data loader for validation files
         :param loss_criterion: loss objective function to be utilized for backprop
         :param step: epoch step number
         :param device: GPU or CPU device
         :return: returns the tuple of ( average validation loss, an image batch example, a text batch example).
                  image and text batches are used as template to save the model if it reaches the optimum
    '''

    loss_meter = AvgMeter()
    target_loss_meter = [ AvgMeter() for i in target_index]


    tqdm_object = tqdm(valid_loader, total=len(valid_loader))
    for image_batch, label_batch in tqdm_object:
        output = model(image_batch.to(device), None, label_batch.to(device))
        loss = loss_criterion(output,label_batch.to(device))

        count = image_batch.size(0)
        loss_meter.update(loss[0].item(), count)
        for idx,tlm in enumerate(target_loss_meter):
            tlm.update(loss[1][idx].item(), count)

        avgs = [tlm.avg for tlm in target_loss_meter]
        valid_loss= {'valid_loss': loss_meter.avg,
                     'target_losses': avgs}

        tqdm_object.set_postfix(valid_loss)
    return loss_meter, image_batch.to(device)




def inference_mlp_head(model, eval_loader, device):
    '''
         THIS FUNCTION RUNS THE MLP BASED INFERENCE
         :param model: CLIP model to be fine-tuned
         :param eval_loader: data loader for evaluation files
         :param device: GPU or CPU device
         :return: returns the tuple of ( ground truth labels, predicted labels) for the evaluation samples
    '''
    #_,train_proj, y_train = get_features_projections(train_loader, model, device)
    test_proj, y_test = get_projections(eval_loader, model, device)




    return y_test, test_proj


def inference_reporting(y_true, y_pred, output_template, target_index):
    '''
         THIS FUNCTION GENERATES THE REPORTING FILES FOR PERFORMANCE COMPARISON
         :param y_true: ground truth labels
         :param y_pred: predicted labels
         :param class_names: name of the classes in training and evaluation folders
         :param output_template: file name template to save the fine-tuned models and inference statistics
         :return: returns weighted F1 score
    '''

    y_base = np.array([121764.2 / 1731.0, 394876.1 / 1731.0, 275875.1 / 1731.0, 11747.67 / 1731.0]) / np.array([325.0, 625.0, 400.0, 7.8])
    y_base = y_base[target_index]
    mse = np.mean((y_true - y_pred) ** 2, axis=0)
    mse_b = np.mean((y_true - y_base) ** 2, axis=0)
    scores = mse / mse_b  # np.array([1100.0, 2500.0, 2000.0, 3.0])
    # Calculate the final score
    final_score = np.mean(scores)
    header = ['output_template', 'seperate_scores', 'sum_score']
    info = [output_template, scores, final_score]
    if not os.path.exists(args.log_dir + 'logs.csv'):
        with open(args.log_dir + 'logs.csv', 'w') as file:
            logger = csv.writer(file)
            logger.writerow(header)
            logger.writerow(info)
    else:
        with open(args.log_dir + 'logs.csv', 'a') as file:
            logger = csv.writer(file)
            logger.writerow(info)

    return final_score

def prepare_submission(predictions,log_args,target_index):
    print('\n\nSUBMISSION SESSION STARTED!\n\n')

    #constants=np.array([325.0, 625.0, 400.0, 7.8])

    template = np.zeros([len(predictions),4])

    template[:,target_index] = predictions

    template = template * np.array([325.0, 625.0, 400.0, 7.8])

    sample_index = np.expand_dims(np.arange(0,len(template)), 1)
    template = np.concatenate((sample_index, template), axis=1)

    submission = pd.DataFrame(data=template, columns=['temp_index', "P", "K", "Mg", "pH"])
    submission = submission.sort_values(by='temp_index', ascending=True)
    submission = submission.drop(columns='temp_index')
    submission.to_csv('{}_submission.csv'.format(log_args), index_label="sample_index")



def get_projections(data_loader, model, device):
    '''
        THIS FUNCTION EXTRACTS THE FEATURES OF GIVEN IMAGES BY USING IMAGE_ENCODER METHOD OF THE GIVEN MODEL
        :param data_loader: data loader in CLIPDataloader object type
        :param model: CLIP model to be exploited for feature extraction
        :param device: GPU or CPU device for placing the model
        :return: returns the tuple of (extracted features, projected features to class IDs, labels)
    '''

    all_projections = []
    all_labels = []

    with torch.no_grad():
        for image_batch, label_batch in tqdm(data_loader):
            features = model.encode_image(image_batch.to(device))
            projections = model.project_image(features)


            all_projections.append(projections)
            all_labels.append(label_batch)

    return torch.cat(all_projections).cpu().numpy(), torch.cat(all_labels).cpu().numpy()


def set_vit_trainable_layers(model, trainable_layers, downstream_task):
    '''
        THIS FUNCTION SET ONLY GIVEN LAYERS NAMES AND INDICES AS TRAINABLE AND FREEZE THE REST OF THE MODEL
        :param model: CLIP model to be exploited for feature extraction
        :param trainable_layers: total number of trainable layers to be activated in the final part of the architecture
        :param downstream_task: one of few-shot learning tasks 'DEFAULT', 'ARC_HEAD','MLP_HEAD','ARC_MLP_HEAD'
        :return:
    '''

    if trainable_layers != -12:
        for idx, param in enumerate(model.parameters()):
            param.requires_grad = False
    else:
        for idx, param in enumerate(model.parameters()):
            param.requires_grad = True

        for idx, param in enumerate(model.transformer.parameters()):
            param.requires_grad = False


    for idx, param in enumerate(model.visual.conv1.parameters()):
        param.requires_grad = True

    for idx, param in enumerate(model.visual.transformer.resblocks[trainable_layers:].parameters()):
        param.requires_grad = True
    for idx, param in enumerate(model.visual.ln_post.parameters()):
        param.requires_grad = True
    for idx, param in enumerate(model.ln_final.parameters()):
        param.requires_grad = True




    if downstream_task == TaskType.HYPERVIEW:
        for idx, param in enumerate(model.external_image_mlp_header.parameters()):
            param.requires_grad = True
        for idx, param in enumerate(model.external_dim_reduction.parameters()):
            param.requires_grad = True



if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument('--train-dir', type=str, default='data/train_data/train_data/')
    parser.add_argument('--label-dir', type=str, default='data/train_data/train_gt.csv')
    parser.add_argument('--eval-dir', type=str, default='data/test_data/')
    parser.add_argument('-b', '--batch-size', default=32, type=int, metavar='BS',help='number of batch size (default: 32)')
    parser.add_argument('--log-dir', type=str, default='logs/')
    parser.add_argument('--total-epoch', type=int, default=60)
    parser.add_argument('--num-workers', type=int, default=8)
    parser.add_argument('--target-index', type=int,nargs='+', default=[0,1,2,3])
    parser.add_argument('--trainable-layers', type=int, nargs='+', default=[-1, -2, -3, -4, -8, -12])
    parser.add_argument('--n-trials', type=int, default=10)
    parser.add_argument('--learning-rate', type=float, nargs='+', default=[1e-3, 5e-4, 1e-4, 5e-5])
    parser.add_argument('--penalty-rate', type=float, nargs='+', default=[1e-2, 5e-3, 1e-3, 5e-4])



    args = parser.parse_args()

    main(args)
