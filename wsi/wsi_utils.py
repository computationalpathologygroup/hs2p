import cv2
import sys
import math
import tqdm
import time
import h5py
import openslide
import numpy as np

from PIL import Image
from pathlib import Path


def compute_time(start_time, end_time):
    elapsed_time = end_time - start_time
    elapsed_mins = int(elapsed_time / 60)
    elapsed_secs = int(elapsed_time - (elapsed_mins * 60))
    return elapsed_mins, elapsed_secs


def isWhitePatch(patch, satThresh=5):
    patch_hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
    return True if np.mean(patch_hsv[:,:,1]) < satThresh else False


def isBlackPatch(patch, rgbThresh=40):
    return True if np.all(np.mean(patch, axis = (0,1)) < rgbThresh) else False


def isBlackPatch_S(patch, rgbThresh=20, percentage=0.05):
    num_pixels = patch.size[0] * patch.size[1]
    return True if np.all(np.array(patch) < rgbThresh, axis=(2)).sum() > num_pixels * percentage else False


def isWhitePatch_S(patch, rgbThresh=220, percentage=0.2):
    num_pixels = patch.size[0] * patch.size[1]
    return True if np.all(np.array(patch) > rgbThresh, axis=(2)).sum() > num_pixels * percentage else False


def savePatchIter_bag_hdf5(patch):
    x, y, cont_idx, patch_size, patch_level, downsample, downsampled_level_dim, level_dim, img_patch, name, save_path= tuple(patch.values())
    img_patch = np.array(img_patch)[np.newaxis,...]
    img_shape = img_patch.shape

    file_path = Path(save_path, f'{name}.h5')
    file = h5py.File(file_path, "a")

    dset = file['imgs']
    dset.resize(len(dset) + img_shape[0], axis=0)
    dset[-img_shape[0]:] = img_patch

    if 'coords' in file:
        coord_dset = file['coords']
        coord_dset.resize(len(coord_dset) + img_shape[0], axis=0)
        coord_dset[-img_shape[0]:] = (x,y)

    file.close()


def save_hdf5(output_path, asset_dict, attr_dict=None, mode='a'):
    file = h5py.File(output_path, mode)
    for key, val in asset_dict.items():
        data_shape = val.shape
        if key not in file:
            data_type = val.dtype
            chunk_shape = (1, ) + data_shape[1:]
            maxshape = (None, ) + data_shape[1:]
            dset = file.create_dataset(key, shape=data_shape, maxshape=maxshape, chunks=chunk_shape, dtype=data_type)
            dset[:] = val
            if attr_dict is not None:
                for attr_key, attr_val in attr_dict[key].items():
                    dset.attrs[attr_key] = attr_val
        else:
            dset = file[key]
            dset.resize(len(dset) + data_shape[0], axis=0)
            dset[-data_shape[0]:] = val
    file.close()
    return output_path


def save_patch(cont_idx, n_contours, wsi, save_dir, asset_dict, attr_dict=None, tqdm_position=-1, tqdm_output_fp=None, fmt='png'):
    coords = asset_dict['coords']
    patch_size = attr_dict['coords']['patch_size']
    patch_level = attr_dict['coords']['patch_level']
    wsi_name = attr_dict['coords']['wsi_name']

    npatch = len(coords)
    start_time = time.time()

    tqdm_file = open(tqdm_output_fp, 'a') if tqdm_output_fp is not None else sys.stderr

    with tqdm.tqdm(
        coords,
        desc=(f'\tSaving {npatch} patch for contour {cont_idx}/{n_contours}'),
        unit=' patch',
        ncols=100,
        position=tqdm_position,
        file=tqdm_file,
        leave=False,
    ) as t:

        for coord in t:
            pil_patch = wsi.read_region(tuple(coord), patch_level, (patch_size, patch_size)).convert("RGB")
            save_path = Path(save_dir, f'{coord[0]}_{coord[1]}.{fmt}')
            pil_patch.save(save_path)

    end_time = time.time()
    patch_saving_mins, patch_saving_secs = compute_time(start_time, end_time)
    tqdm_file.close()
    return npatch, patch_saving_mins, patch_saving_secs


def initialize_hdf5_bag(first_patch, save_coord=False):
    x, y, cont_idx, patch_size, patch_level, downsample, downsampled_level_dim, level_dim, img_patch, name, save_path = tuple(first_patch.values())
    file_path = Path(save_path, f'{name}.h5')
    file = h5py.File(file_path, "w")
    img_patch = np.array(img_patch)[np.newaxis,...]
    dtype = img_patch.dtype

    # Initialize a resizable dataset to hold the output
    img_shape = img_patch.shape
    maxshape = (None,) + img_shape[1:] #maximum dimensions up to which dataset maybe resized (None means unlimited)
    dset = file.create_dataset('imgs', shape=img_shape, maxshape=maxshape,  chunks=img_shape, dtype=dtype)

    dset[:] = img_patch
    dset.attrs['patch_size'] = patch_size
    dset.attrs['patch_level'] = patch_level
    dset.attrs['wsi_name'] = name
    dset.attrs['downsample'] = downsample
    dset.attrs['level_dim'] = level_dim
    dset.attrs['downsampled_level_dim'] = downsampled_level_dim

    if save_coord:
        coord_dset = file.create_dataset('coords', shape=(1, 2), maxshape=(None, 2), chunks=(1, 2), dtype=np.int32)
        coord_dset[:] = (x,y)

    file.close()
    return file_path


def DrawGrid(img, coord, shape, thickness=2, color=(0,0,0,255)):
    cv2.rectangle(img, tuple(np.maximum([0, 0], coord-thickness//2)), tuple(coord - thickness//2 + np.array(shape)), (0, 0, 0, 255), thickness=thickness)
    return img


def DrawMap(canvas, patch_dset, coords, patch_size, indices=None, verbose=False, draw_grid=True):
    if indices is None:
        indices = np.arange(len(coords))
    total = len(indices)
    if verbose:
        ten_percent_chunk = math.ceil(total * 0.1)
        print(f'Start stitching {patch_dset.attrs["wsi_name"]}...')

    with tqdm.tqdm(
        range(total),
        desc=(f'Stitching'),
        unit=' patch',
        ncols=80,
        position=1,
        leave=True,
    ) as t:

        for idx in t:

            patch_id = indices[idx]
            patch = patch_dset[patch_id]
            patch = cv2.resize(patch, patch_size)
            coord = coords[patch_id]
            canvas_crop_shape = canvas[coord[1]:coord[1]+patch_size[1], coord[0]:coord[0]+patch_size[0], :3].shape[:2]
            canvas[coord[1]:coord[1]+patch_size[1], coord[0]:coord[0]+patch_size[0], :3] = patch[:canvas_crop_shape[0], :canvas_crop_shape[1], :]
            if draw_grid:
                DrawGrid(canvas, coord, patch_size)

    return Image.fromarray(canvas)


def DrawMapFromCoords(
    canvas,
    wsi_object,
    coords,
    patch_size,
    vis_level,
    indices=None,
    draw_grid=True,
    tqdm_position=-1,
    tqdm_output_fp=None,
    verbose=False,
    ):

    downsamples = wsi_object.wsi.level_downsamples[vis_level]
    if indices is None:
        indices = np.arange(len(coords))
    total = len(indices)

    patch_size = tuple(np.ceil((np.array(patch_size)/np.array(downsamples))).astype(np.int32))
    if verbose:
        print(f'downscaled patch size: {patch_size}')

    tqdm_file = open(tqdm_output_fp, 'a') if tqdm_output_fp is not None else sys.stderr

    with tqdm.tqdm(
        range(total),
        desc=(f'Stitching'),
        unit=' patch',
        ncols=80,
        position=tqdm_position,
        file=tqdm_file,
        leave=False,
    ) as t:

        for idx in t:

            patch_id = indices[idx]
            coord = coords[patch_id]
            patch = np.array(wsi_object.wsi.read_region(tuple(coord), vis_level, patch_size).convert("RGB"))
            coord = np.ceil(coord / downsamples).astype(np.int32)
            canvas_crop_shape = canvas[coord[1]:coord[1]+patch_size[1], coord[0]:coord[0]+patch_size[0], :3].shape[:2]
            canvas[coord[1]:coord[1]+patch_size[1], coord[0]:coord[0]+patch_size[0], :3] = patch[:canvas_crop_shape[0], :canvas_crop_shape[1], :]
            if draw_grid:
                DrawGrid(canvas, coord, patch_size)

    tqdm_file.close()

    return Image.fromarray(canvas)


def StitchPatches(hdf5_file_path, downscale=16, draw_grid=False, bg_color=(0,0,0), alpha=-1):
    file = h5py.File(hdf5_file_path, 'r')
    dset = file['imgs']
    coords = file['coords'][:]
    if 'downsampled_level_dim' in dset.attrs.keys():
        w, h = dset.attrs['downsampled_level_dim']
    else:
        w, h = dset.attrs['level_dim']
    print(f'original size: {w} x {h}')
    w = w // downscale
    h = h //downscale
    coords = (coords / downscale).astype(np.int32)
    print(f'downscaled size for stiching: {w} x {h}')
    print(f'number of patches: {len(dset)}')
    img_shape = dset[0].shape
    print(f'patch shape: {img_shape}')
    downscaled_shape = (img_shape[1] // downscale, img_shape[0] // downscale)

    if w*h > Image.MAX_IMAGE_PIXELS:
        raise Image.DecompressionBombError("Visualization Downscale %d is too large" % downscale)

    if alpha < 0 or alpha == -1:
        heatmap = Image.new(size=(w,h), mode="RGB", color=bg_color)
    else:
        heatmap = Image.new(size=(w,h), mode="RGBA", color=bg_color + (int(255 * alpha),))

    heatmap = np.array(heatmap)
    heatmap = DrawMap(heatmap, dset, coords, downscaled_shape, indices=None, draw_grid=draw_grid)

    file.close()
    return heatmap


def StitchCoords(hdf5_file_path, wsi_object, downscale=16, draw_grid=False, bg_color=(0,0,0), alpha=-1, tqdm_position=-1, tqdm_output_fp=None, verbose=False):
    wsi = wsi_object.getOpenSlide()
    vis_level = wsi.get_best_level_for_downsample(downscale)
    file = h5py.File(hdf5_file_path, 'r')
    dset = file['coords']
    coords = dset[:]
    w, h = wsi.level_dimensions[0]

    # print(f'Start stitching {dset.attrs["wsi_name"]}...')
    if verbose:
        print(f'original size: {w} x {h}')

    w, h = wsi.level_dimensions[vis_level]

    patch_size = dset.attrs['patch_size']
    patch_level = dset.attrs['patch_level']
    if verbose:
        print(f'downscaled size for stiching: {w} x {h}')
        print(f'number of patches: {len(coords)}')
        print(f'patch size: {patch_size}')
        print(f'patch level: {patch_level}')

    patch_size = tuple((np.array((patch_size, patch_size)) * wsi.level_downsamples[patch_level]).astype(np.int32))
    if verbose:
        print(f'ref patch size: {patch_size}')

    if w*h > Image.MAX_IMAGE_PIXELS:
        raise Image.DecompressionBombError("Visualization Downscale %d is too large" % downscale)

    if alpha < 0 or alpha == -1:
        heatmap = Image.new(size=(w,h), mode="RGB", color=bg_color)
    else:
        heatmap = Image.new(size=(w,h), mode="RGBA", color=bg_color + (int(255 * alpha),))

    heatmap = np.array(heatmap)
    heatmap = DrawMapFromCoords(heatmap, wsi_object, coords, patch_size, vis_level, indices=None, draw_grid=draw_grid, tqdm_position=tqdm_position, tqdm_output_fp=tqdm_output_fp, verbose=verbose)

    file.close()
    # print('Done!')
    return heatmap
