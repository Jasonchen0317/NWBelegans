from Neurons import Neuron, Image
import pyro.distributions as dist
from scipy.io import loadmat
import torch
import numpy as np
import os
from pynwb import NWBHDF5IO
import pandas as pd
import h5py
from dandi.dandiapi import DandiAPIClient
import remfile
import pickle

from pynwb import load_namespaces, get_class, register_class, NWBFile, TimeSeries, NWBHDF5IO
from pynwb.file import MultiContainerInterface, NWBContainer, Device, Subject
from pynwb.ophys import ImageSeries, OnePhotonSeries, OpticalChannel, ImageSegmentation, PlaneSegmentation, Fluorescence, DfOverF, CorrectedImageStack, MotionCorrection, RoiResponseSeries, ImagingPlane
from pynwb.core import NWBDataInterface
from pynwb.epoch import TimeIntervals
from pynwb.behavior import SpatialSeries, Position
from pynwb.image import ImageSeries

from ndx_multichannel_volume import CElegansSubject, OpticalChannelReferences, OpticalChannelPlus, ImagingVolume, VolumeSegmentation, MultiChannelVolume, MultiChannelVolumeSeries

def load_NWB(datapath, folders, match, group_assigns, bodypart='head', histmatched=True, skipfiles=[]):

    ims = []

    for i, folder in enumerate(folders):
        to_match = match[i]
        if folder.split(':')[0] == 'dandi': #if accessing from Dandi folder
            dandi_id = folder.split(':')[1]
            with DandiAPIClient() as client:
                dandiset = client.get_dandiset(dandi_id, 'draft')
                for asset in dandiset.get_assets():
                    s3_url = asset.get_content_url(follow_redirects=1, strip_query=True)
                    file = remfile.File(s3_url)

                    with h5py.File(file, 'r') as f:
                        with NWBHDF5IO(file=f, mode='r',load_namespaces=True) as io:
                            read_nwb = io.read()
                            identifier = read_nwb.identifier
                            seg = read_nwb.processing['NeuroPAL']['NeuroPALSegmentation']['NeuroPALNeurons'].voxel_mask[:]
                            labels = read_nwb.processing['NeuroPAL']['NeuroPALSegmentation']['NeuroPALNeurons']['ID_labels'][:]
                            channels = read_nwb.acquisition['NeuroPALImageRaw'].RGBW_channels[:] #get which channels of the image correspond to which RGBW pseudocolors
                            image = read_nwb.acquisition['NeuroPALImageRaw'].data[:]
                            scale = read_nwb.imaging_planes['NeuroPALImVol'].grid_spacing[:] #get which channels of the image correspond to which RGBW pseudocolors

                        print(identifier)

                        if identifier in skipfiles:
                            continue

                        labels = ["".join(label) for label in labels]

                        labels = [label[:-1] if label.endswith('?') else label for label in labels]

                        blobs = pd.DataFrame.from_records(seg, columns = ['X', 'Y', 'Z', 'weight'])
                        blobs = blobs.drop(['weight'], axis=1)

                        histmatch_mat = loadmat('/Users/danielysprague/foco_lab/data/hist_matched_test3/'+identifier+'.mat')

                        if dandi_id == '000692':
                            RGBW = histmatch_mat['data_matched']
                        else:
                            RGBW = np.transpose(histmatch_mat['data_matched'], (1,0,2,3))

                        if not histmatched:
                            print('not matched')
                            RGB = image[:,:,:,channels[:-1]]
                            zscore_RGB = Zscore_frame(RGB)

                        else:
                            print('matched')
                            zscore_RGB = Zscore_frame(RGBW[:,:,:]) 

                        if dandi_id == '000565':
                            with open('/Users/danielysprague/foco_lab/data/SK1_crop.pkl', 'rb') as f:
                                cropped_dict = pickle.load(f)
                            start_x = int(cropped_dict[identifier][0])
                            end_x = int(cropped_dict[identifier][1])
                            blobs = blobs[(blobs['x']>start_x)&(blobs['x']<end_x)]
                            idx_keep = [i for i, row in blobs.iterrows() if (row['x']>start_x) and (row['x']<end_x)]
                            blobs['x'] = blobs['x'] - start_x
                            blobs=blobs.reset_index()
                            labels = [labels[i] for i in idx_keep]

                        idx_keep = [i for i, row in blobs.iterrows() if (row['x']<RGBW.shape[0]) and (row['y']<RGBW.shape[1]) and (row['z']<RGBW.shape[2])]
                        blobs = blobs[(blobs['x']<RGBW.shape[0])&(blobs['y']<RGBW.shape[1])&(blobs['z']<RGBW.shape[2])]

                        blobs[['R','G','B']] = [zscore_RGB[row['x'],row['y'],row['z'],:] for i, row in blobs.iterrows()]
                        blobs[['xr', 'yr', 'zr']] = [[row['x']*scale[0],row['y']*scale[1], row['z']*scale[2]] for i, row in blobs.iterrows()]
                        blobs['ID'] = [labels[i] for i in idx_keep]

                        neurons = []
                        for i, row in blobs.iterrows():
                            neuron = Neuron.Neuron()
                            # Neuron position & color
                            pixpos = np.asarray([row['x'], row['y'], row['z']])

                            neuron.position = np.asarray(row[['xr','yr', 'zr']])
                            neuron.color = np.asarray(row[['R','G','B']])
                            cpatch = subcube(zscore_RGB, pixpos, np.asarray([1,1,0]))
                            neuron.color_readout = np.median(np.reshape(cpatch, (cpatch.shape[0]*cpatch.shape[1]*cpatch.shape[2],cpatch.shape[3])),axis=0) 
                            # User neuron ID
                            neuron.annotation = row['ID']
                            neuron.annotation_confidence = .99

                            if neuron.annotation == 'AS2' or neuron.annotation == 'AVK' or '?' in neuron.annotation or neuron.annotation=='IL1V': #AS2 causing issues across the board so just removing. Nemanode does not consider it a head neuron, AVK causing similar problems
                                continue
                            
                            neurons.append(neuron)

                        filename = identifier
                        group = group_assigns[filename]

                        im = Image.Image(bodypart,neurons, filename= filename, group= group, match = to_match)
                        ims.append(im)

        else:
            for file in os.listdir(datapath+'/'+folder):
                print(file)

                if not file[-4:] =='.nwb':
                    continue

                filepath = datapath + '/' + folder + '/' +file

                with NWBHDF5IO(filepath, mode='r', load_namespaces=True) as io:
                    read_nwb = io.read()
                    identifier = read_nwb.identifier
                    seg = read_nwb.processing['NeuroPAL']['NeuroPALSegmentation']['NeuroPALNeurons'].voxel_mask[:]
                    labels = read_nwb.processing['NeuroPAL']['NeuroPALSegmentation']['NeuroPALNeurons']['ID_labels'][:]
                    channels = read_nwb.acquisition['NeuroPALImageRaw'].RGBW_channels[:] #get which channels of the image correspond to which RGBW pseudocolors
                    image = read_nwb.acquisition['NeuroPALImageRaw'].data[:]
                    scale = read_nwb.imaging_planes['NeuroPALImVol'].grid_spacing[:] #get which channels of the image correspond to which RGBW pseudocolors
            
                if identifier in skipfiles:
                    continue
                
                labels = ["".join(label) for label in labels]

                labels = [label[:-1] if label.endswith('?') else label for label in labels]

                blobs = pd.DataFrame.from_records(seg, columns = ['X', 'Y', 'Z', 'weight'])
                blobs = blobs.drop(['weight'], axis=1)

                histmatch_mat = loadmat('/Users/danielysprague/foco_lab/data/hist_matched_test3/'+file[:-4]+'.mat')

                RGBW = np.transpose(histmatch_mat['data_matched'], (1,0,2,3))

                if not histmatched:
                    print('not matched')
                    RGB = image[:,:,:,channels[:-1]]
                    zscore_RGB = Zscore_frame(RGB)

                else:
                    print('matched')
                    zscore_RGB = Zscore_frame(RGBW[:,:,:])

                if folder == 'SK1':
                    with open('/Users/danielysprague/foco_lab/data/SK1_crop.pkl', 'rb') as f:
                        cropped_dict = pickle.load(f)
                    start_x = int(cropped_dict[identifier][0])
                    end_x = int(cropped_dict[identifier][1])
                    blobs = blobs[(blobs['x']>start_x)&(blobs['x']<end_x)]
                    idx_keep = [i for i, row in blobs.iterrows() if (row['x']>start_x) and (row['x']<end_x)]
                    blobs['x'] = blobs['x'] - start_x
                    blobs=blobs.reset_index()
                    labels = [labels[i] for i in idx_keep]

                idx_keep = [i for i, row in blobs.iterrows() if (row['x']<RGBW.shape[0]) and (row['y']<RGBW.shape[1]) and (row['z']<RGBW.shape[2])]
                blobs = blobs[(blobs['x']<RGBW.shape[0])&(blobs['y']<RGBW.shape[1])&(blobs['z']<RGBW.shape[2])]

                blobs[['R','G','B']] = [zscore_RGB[row['x'],row['y'],row['z'],:] for i, row in blobs.iterrows()]
                blobs[['xr', 'yr', 'zr']] = [[row['x']*scale[0],row['y']*scale[1], row['z']*scale[2]] for i, row in blobs.iterrows()]
                blobs['ID'] = [labels[i] for i in idx_keep]

                neurons = []
                for i, row in blobs.iterrows():
                    neuron = Neuron.Neuron()
                    # Neuron position & color
                    pixpos = np.asarray([row['x'], row['y'], row['z']])

                    neuron.position = np.asarray(row[['xr','yr', 'zr']])
                    neuron.color = np.asarray(row[['R','G','B']])
                    cpatch = subcube(zscore_RGB, pixpos, np.asarray([1,1,0]))
                    neuron.color_readout = np.median(np.reshape(cpatch, (cpatch.shape[0]*cpatch.shape[1]*cpatch.shape[2],cpatch.shape[3])),axis=0) 
                    # User neuron ID
                    neuron.annotation = row['ID']
                    neuron.annotation_confidence = .99

                    if neuron.annotation == 'AS2' or neuron.annotation == 'AVK' or '?' in neuron.annotation or neuron.annotation=='IL1V': #AS2 causing issues across the board so just removing. Nemanode does not consider it a head neuron, AVK causing similar problems
                        continue
                    
                    neurons.append(neuron)

                filename = identifier
                group = group_assigns[filename]

                im = Image.Image(bodypart,neurons, filename= filename, group= group, match = to_match)
                ims.append(im)

    return ims

def subcube(cube, loc, center):
    # Grabbing a patch of the image in a given location.
    # Amin Nejat

    sz = np.asarray([cube.shape[0], cube.shape[1], cube.shape[2]])

    rel = loc-center
    reu = loc+center


    rel[loc - center < 0] = 0
    reu[loc + center +1 - sz > 0] = sz[loc + center +1 - sz > 0]-1

    patch = cube[rel[0]: reu[0]+1,
                 rel[1]: reu[1]+1,
                 rel[2]: reu[2]+1]

    newcenter = [patch.shape[0], patch.shape[1], patch.shape[2]]

    if any(newcenter[0:3] != 2 * np.round(center) + 1):
        pre = np.where(loc-center<0, center-loc, 0)
        post = np.where(loc+center+1-sz>0, loc+center+1-sz, 0)

        patch = np.pad(patch, ((int(pre[0]), int(post[0])),
                                (int(pre[1]), int(post[1])),
                                (int(pre[2]), int(post[2])),
                                (0,0)), 'constant')

    return patch

def Zscore_frame(volume): #color channels must be in last dimension

    zscore_data = np.zeros(volume.shape)

    for i in range(volume.shape[-1]):
        coldata = volume[:,:,:,i]
        zscore_data[:,:,:,i] = (coldata-np.mean(coldata))/np.std(coldata)

    return zscore_data

if __name__ == '__main__':

    datapath = '/Users/danielysprague/foco_lab/data'
    bodypart = 'head'
    folders = ['NWB_chaudhary']

    ims = load_NWB(datapath, folders, bodypart=bodypart)