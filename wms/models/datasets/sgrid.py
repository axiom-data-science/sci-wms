# -*- coding: utf-8 -*-
import os
import time
import shutil
import bisect
import tempfile
from math import sqrt

import numpy as np
import netCDF4 as nc4
from pyaxiom.netcdf import EnhancedDataset, EnhancedMFDataset
from pysgrid import load_grid
from pysgrid.read_netcdf import NetCDFDataset as SGrid
from pysgrid.processing_2d import avg_to_cell_center, rotate_vectors

import pandas as pd

from rtree import index

from django.core.cache import caches

from wms import mpl_handler
from wms import gfi_handler
from wms import data_handler
from wms import gmd_handler

from wms.models import Dataset, Layer, VirtualLayer, NetCDFDataset
from wms.utils import DotDict, calc_lon_lat_padding, calc_safety_factor, find_appropriate_time

from wms import logger


class SGridDataset(Dataset, NetCDFDataset):

    @classmethod
    def is_valid(cls, uri):
        try:
            with EnhancedDataset(uri) as ds:
                try:
                    SGrid(ds)
                    return True
                except ValueError:
                    if 'sgrid' in ds.Conventions.lower():
                        return True
                    else:
                        return False
        except RuntimeError:
            try:
                with EnhancedMFDataset(uri, aggdim='time') as ds:
                    try:
                        SGrid(ds)
                        return True
                    except ValueError:
                        if 'sgrid' in ds.Conventions.lower():
                            return True
                        else:
                            return False
            except (IndexError, AttributeError, RuntimeError, ValueError):
                return False
        except (FileNotFoundError, AttributeError):
            return False

    def has_grid_cache(self):
        return all([
            os.path.exists(self.topology_file),
            os.path.exists(self.face_tree_data_file),
            os.path.exists(self.face_tree_index_file)
        ])

    def has_time_cache(self):
        return caches['time'].get(self.time_cache_file) is not None

    def clear_cache(self):
        super().clear_cache()
        return caches['time'].delete(self.time_cache_file)

    def make_rtree(self):

        with self.dataset() as nc:
            sg = load_grid(nc)

            def rtree_generator_function():
                c = 0
                centers = np.dstack((sg.center_lon, sg.center_lat))
                for i, axis in enumerate(centers):
                    for j, (x, y) in enumerate(axis):
                        c += 1
                        yield (c, (x, y, x, y), (i, j))

            logger.info("Building Faces (centers) Rtree Topology Cache for {0}".format(self.name))
            _, temp_file = tempfile.mkstemp(suffix='.face')
            start = time.time()
            p = index.Property()
            p.filename = str(temp_file)
            p.overwrite = True
            p.storage   = index.RT_Disk
            p.dimension = 2
            idx = index.Index(p.filename,
                              rtree_generator_function(),
                              properties=p,
                              overwrite=True,
                              interleaved=True)
            idx.close()

            logger.info("Built Faces (centers) Rtree Topology Cache in {0} seconds.".format(time.time() - start))
            shutil.move('{}.dat'.format(temp_file), self.face_tree_data_file)
            shutil.move('{}.idx'.format(temp_file), self.face_tree_index_file)

    def update_time_cache(self):
        with self.dataset() as nc:
            if nc is None:
                logger.error("Failed update_time_cache, could not load dataset "
                             "as a netCDF4 object")
                return

            time_cache = {}
            layer_cache = {}
            time_vars = nc.get_variables_by_attributes(standard_name='time')
            for time_var in time_vars:
                time_cache[time_var.name] = nc4.num2date(
                    time_var[:],
                    time_var.units,
                    getattr(time_var, 'calendar', 'standard')
                )

            for ly in self.all_layers():
                try:
                    layer_cache[ly.access_name] = find_appropriate_time(nc.variables[ly.access_name], time_vars)
                except ValueError:
                    layer_cache[ly.access_name] = None

            full_cache = {'times': time_cache, 'layers': layer_cache}
            logger.info("Built time cache for {0}".format(self.name))
            caches['time'].set(self.time_cache_file, full_cache, None)
            return full_cache

    def update_grid_cache(self, force=False):
        with self.dataset() as nc:
            if nc is None:
                logger.error("Failed update_grid_cache, could not load dataset "
                             "as a netCDF4 object")
                return
            sg = load_grid(nc)

            # Atomic write
            tmphandle, tmpsave = tempfile.mkstemp()
            try:
                sg.save_as_netcdf(tmpsave)
            finally:
                os.close(tmphandle)
                if os.path.isfile(tmpsave):
                    shutil.move(tmpsave, self.topology_file)
                else:
                    logger.error("Failed to create topology_file cache for Dataset '{}'".format(self.dataset.name))
                    return

        # Now do the RTree index
        self.make_rtree()

    def minmax(self, layer, request):
        time_index, time_value = self.nearest_time(layer, request.GET['time'])
        wgs84_bbox = request.GET['wgs84_bbox']

        with self.dataset() as nc:
            cached_sg = load_grid(self.topology_file)
            lon_name, lat_name = cached_sg.face_coordinates
            lon_obj = getattr(cached_sg, lon_name)
            lat_obj = getattr(cached_sg, lat_name)
            lon = cached_sg.center_lon[lon_obj.center_slicing]
            lat = cached_sg.center_lat[lat_obj.center_slicing]
            spatial_idx = data_handler.lat_lon_subset_idx(lon, lat,
                                                          lonmin=wgs84_bbox.minx,
                                                          latmin=wgs84_bbox.miny,
                                                          lonmax=wgs84_bbox.maxx,
                                                          latmax=wgs84_bbox.maxy)
            subset_lon = np.unique(spatial_idx[0])
            subset_lat = np.unique(spatial_idx[1])
            grid_variables = cached_sg.grid_variables

            vmin = None
            vmax = None
            raw_data = None
            if isinstance(layer, Layer):
                data_obj = getattr(cached_sg, layer.access_name)
                raw_var = nc.variables[layer.access_name]
                if len(raw_var.shape) == 4:
                    z_index, z_value = self.nearest_z(layer, request.GET['elevation'])
                    raw_data = raw_var[time_index, z_index, subset_lon, subset_lat]
                elif len(raw_var.shape) == 3:
                    raw_data = raw_var[time_index, subset_lon, subset_lat]
                elif len(raw_var.shape) == 2:
                    raw_data = raw_var[subset_lon, subset_lat]
                else:
                    raise BaseException('Unable to trim variable {0} data.'.format(layer.access_name))

                # handle grid variables
                if set([layer.access_name]).issubset(grid_variables):
                    raw_data = avg_to_cell_center(raw_data, data_obj.center_axis)

                vmin = np.nanmin(raw_data).item()
                vmax = np.nanmax(raw_data).item()

            elif isinstance(layer, VirtualLayer):
                x_var = None
                y_var = None
                raw_vars = []
                for l in layer.layers:
                    data_obj = getattr(cached_sg, l.access_name)
                    raw_var = nc.variables[l.access_name]
                    raw_vars.append(raw_var)
                    if len(raw_var.shape) == 4:
                        z_index, z_value = self.nearest_z(layer, request.GET['elevation'])
                        raw_data = raw_var[time_index, z_index, subset_lon, subset_lat]
                    elif len(raw_var.shape) == 3:
                        raw_data = raw_var[time_index, subset_lon, subset_lat]
                    elif len(raw_var.shape) == 2:
                        raw_data = raw_var[subset_lon, subset_lat]
                    else:
                        raise BaseException('Unable to trim variable {0} data.'.format(l.access_name))

                    if x_var is None:
                        if data_obj.vector_axis and data_obj.vector_axis.lower() == 'x':
                            x_var = raw_data
                        elif data_obj.center_axis == 1:
                            x_var = raw_data

                    if y_var is None:
                        if data_obj.vector_axis and data_obj.vector_axis.lower() == 'y':
                            y_var = raw_data
                        elif data_obj.center_axis == 0:
                            y_var = raw_data

                if ',' in layer.var_name and raw_data is not None:
                    # Vectors, so return magnitude
                    data = [
                        sqrt((u * u) + (v * v)) for (u, v,) in
                        zip(x_var.flatten(), y_var.flatten()) if u != np.nan and v != np.nan
                    ]
                    vmin = min(data)
                    vmax = max(data)

            return gmd_handler.from_dict(dict(min=vmin, max=vmax))

    def getmap(self, layer, request):
        time_index, time_value = self.nearest_time(layer, request.GET['time'])
        wgs84_bbox = request.GET['wgs84_bbox']

        with self.dataset() as nc:
            cached_sg = load_grid(self.topology_file)
            lon_name, lat_name = cached_sg.face_coordinates
            lon_obj = getattr(cached_sg, lon_name)
            lat_obj = getattr(cached_sg, lat_name)
            lon = cached_sg.center_lon[lon_obj.center_slicing]
            lat = cached_sg.center_lat[lat_obj.center_slicing]

            if isinstance(layer, Layer):
                data_obj = getattr(cached_sg, layer.access_name)
                raw_var = nc.variables[layer.access_name]
                if len(raw_var.shape) == 4:
                    z_index, z_value = self.nearest_z(layer, request.GET['elevation'])
                    raw_data = raw_var[time_index, z_index, data_obj.center_slicing[-2], data_obj.center_slicing[-1]]
                elif len(raw_var.shape) == 3:
                    raw_data = raw_var[time_index, data_obj.center_slicing[-2], data_obj.center_slicing[-1]]
                elif len(raw_var.shape) == 2:
                    raw_data = raw_var[data_obj.center_slicing]
                else:
                    raise BaseException('Unable to trim variable {0} data.'.format(layer.access_name))
                # handle edge variables
                if data_obj.location is not None and 'edge' in data_obj.location:
                    raw_data = avg_to_cell_center(raw_data, data_obj.center_axis)

                if request.GET['image_type'] == 'pcolor':
                    return mpl_handler.pcolormesh_response(lon, lat, data=raw_data, request=request)
                elif request.GET['image_type'] in ['filledhatches', 'hatches', 'filledcontours', 'contours']:
                    return mpl_handler.contouring_response(lon, lat, data=raw_data, request=request)
                else:
                    raise NotImplementedError('Image type "{}" is not supported.'.format(request.GET['image_type']))

            elif isinstance(layer, VirtualLayer):
                x_var = None
                y_var = None
                raw_vars = []
                for l in layer.layers:
                    data_obj = getattr(cached_sg, l.access_name)
                    raw_var = nc.variables[l.access_name]
                    raw_vars.append(raw_var)
                    if len(raw_var.shape) == 4:
                        z_index, z_value = self.nearest_z(layer, request.GET['elevation'])
                        raw_data = raw_var[time_index, z_index, data_obj.center_slicing[-2], data_obj.center_slicing[-1]]
                    elif len(raw_var.shape) == 3:
                        raw_data = raw_var[time_index, data_obj.center_slicing[-2], data_obj.center_slicing[-1]]
                    elif len(raw_var.shape) == 2:
                        raw_data = raw_var[data_obj.center_slicing]
                    else:
                        raise BaseException('Unable to trim variable {0} data.'.format(l.access_name))

                    raw_data = avg_to_cell_center(raw_data, data_obj.center_axis)
                    if x_var is None:
                        if data_obj.vector_axis and data_obj.vector_axis.lower() == 'x':
                            x_var = raw_data
                        elif data_obj.center_axis == 1:
                            x_var = raw_data

                    if y_var is None:
                        if data_obj.vector_axis and data_obj.vector_axis.lower() == 'y':
                            y_var = raw_data
                        elif data_obj.center_axis == 0:
                            y_var = raw_data

                if x_var is None or y_var is None:
                    raise BaseException('Unable to determine x and y variables.')

                dim_lengths = [ len(v.dimensions) for v in raw_vars ]
                if len(list(set(dim_lengths))) != 1:
                    raise AttributeError('One or both of the specified variables has incorrect dimensions.')

                if request.GET['image_type'] == 'vectors':
                    angles = cached_sg.angles[lon_obj.center_slicing]
                    vectorstep = request.GET['vectorstep']
                    # don't do this if the vectorstep is 1; let's save a microsecond or two
                    # it's identical to getting all the data
                    if vectorstep > 1:
                        data_dim = len(lon.shape)
                        step_slice = (np.s_[::vectorstep],) * data_dim  # make sure the vector step is used for all applicable dimensions
                        lon = lon[step_slice]
                        lat = lat[step_slice]
                        x_var = x_var[step_slice]
                        y_var = y_var[step_slice]
                        angles = angles[step_slice]
                    vectorscale = request.GET['vectorscale']
                    padding_factor = calc_safety_factor(vectorscale)
                    # figure out the average distance between lat/lon points
                    # do the math after taking into the vectorstep if specified
                    spatial_idx_padding = calc_lon_lat_padding(lon, lat, padding_factor)
                    spatial_idx = data_handler.lat_lon_subset_idx(lon, lat,
                                                                  lonmin=wgs84_bbox.minx,
                                                                  latmin=wgs84_bbox.miny,
                                                                  lonmax=wgs84_bbox.maxx,
                                                                  latmax=wgs84_bbox.maxy,
                                                                  padding=spatial_idx_padding
                                                                  )
                    subset_lon = self._spatial_data_subset(lon, spatial_idx)
                    subset_lat = self._spatial_data_subset(lat, spatial_idx)
                    # rotate vectors
                    x_rot, y_rot = rotate_vectors(x_var, y_var, angles)
                    spatial_subset_x_rot = self._spatial_data_subset(x_rot, spatial_idx)
                    spatial_subset_y_rot = self._spatial_data_subset(y_rot, spatial_idx)
                    return mpl_handler.quiver_response(subset_lon,
                                                       subset_lat,
                                                       spatial_subset_x_rot,
                                                       spatial_subset_y_rot,
                                                       request,
                                                       vectorscale
                                                       )
                else:
                    raise NotImplementedError('Image type "{}" is not supported.'.format(request.GET['image_type']))

    def getfeatureinfo(self, layer, request):
        with self.dataset() as nc:
            data_obj = nc.variables[layer.access_name]

            geo_index, closest_x, closest_y, start_time_index, end_time_index, return_dates = self.setup_getfeatureinfo(layer, request)

            return_arrays = []
            z_value = None
            if isinstance(layer, Layer):
                if len(data_obj.shape) == 4:
                    z_index, z_value = self.nearest_z(layer, request.GET['elevation'])
                    data = data_obj[start_time_index:end_time_index, z_index, geo_index[0], geo_index[1]]
                elif len(data_obj.shape) == 3:
                    data = data_obj[start_time_index:end_time_index, geo_index[0], geo_index[1]]
                elif len(data_obj.shape) == 2:
                    data = data_obj[geo_index[0], geo_index[1]]
                else:
                    raise ValueError("Dimension Mismatch: data_obj.shape == {0} and time indexes = {1} to {2}".format(data_obj.shape, start_time_index, end_time_index))

                return_arrays.append((layer.var_name, data))

            elif isinstance(layer, VirtualLayer):

                # Data needs to be [var1,var2] where var are 1D (nodes only, elevation and time already handled)
                for l in layer.layers:
                    if len(data_obj.shape) == 4:
                        z_index, z_value = self.nearest_z(layer, request.GET['elevation'])
                        data = data_obj[start_time_index:end_time_index, z_index, geo_index[0], geo_index[1]]
                    elif len(data_obj.shape) == 3:
                        data = data_obj[start_time_index:end_time_index, geo_index[0], geo_index[1]]
                    elif len(data_obj.shape) == 2:
                        data = data_obj[geo_index[0], geo_index[1]]
                    else:
                        raise ValueError("Dimension Mismatch: data_obj.shape == {0} and time indexes = {1} to {2}".format(data_obj.shape, start_time_index, end_time_index))
                    return_arrays.append((l.var_name, data))

            # Data is now in the return_arrays list, as a list of numpy arrays.  We need
            # to add time and depth to them to create a single Pandas DataFrame
            if len(data_obj.shape) == 4:
                df = pd.DataFrame({'time': return_dates,
                                   'x': closest_x,
                                   'y': closest_y,
                                   'z': z_value})
            elif len(data_obj.shape) == 3:
                df = pd.DataFrame({'time': return_dates,
                                   'x': closest_x,
                                   'y': closest_y})
            elif len(data_obj.shape) == 2:
                df = pd.DataFrame({'x': closest_x,
                                   'y': closest_y})
            else:
                df = pd.DataFrame()

            # Now add a column for each member of the return_arrays list
            for (var_name, np_array) in return_arrays:
                df.loc[:, var_name] = pd.Series(np_array, index=df.index)

            return gfi_handler.from_dataframe(request, df)

    def wgs84_bounds(self, layer):
        try:
            cached_sg = load_grid(self.topology_file)
        except BaseException:
            pass
        else:
            lon_name, lat_name = cached_sg.face_coordinates
            lon_var_obj = getattr(cached_sg, lon_name)
            lat_var_obj = getattr(cached_sg, lat_name)
            lon_trimmed = cached_sg.center_lon[lon_var_obj.center_slicing]
            lat_trimmed = cached_sg.center_lat[lat_var_obj.center_slicing]
            lon_max = lon_trimmed.max()
            lon_min = lon_trimmed.min()
            lat_max = lat_trimmed.max()
            lat_min = lat_trimmed.min()
            return DotDict(minx=lon_min,
                           miny=lat_min,
                           maxx=lon_max,
                           maxy=lat_max,
                           bbox=(lon_min, lat_min, lon_max, lat_max)
                           )

    def nearest_z(self, layer, z):
        """
        Return the z index and z value that is closest
        """
        depths = self.depths(layer)
        depth_idx = bisect.bisect_right(depths, z)
        try:
            depths[depth_idx]
        except IndexError:
            depth_idx -= 1
        return depth_idx, depths[depth_idx]

    def times(self, layer):
        time_cache = caches['time'].get(self.time_cache_file, {'times': {}, 'layers': {}})

        if layer.access_name not in time_cache['layers']:
            logger.error("No layer ({}) in time cache, returning nothing".format(layer.access_name))
            return []

        ltv = time_cache['layers'].get(layer.access_name)
        if ltv is None:
            # Legit this might not be a layer with time so just return empty list (no error message)
            return []

        if ltv in time_cache['times']:
            return time_cache['times'][ltv]
        else:
            logger.error("No time ({}) in time cache, returning nothing".format(ltv))
            return []

    def depth_variable(self, layer):
        with self.dataset() as nc:
            try:
                layer_var = nc.variables[layer.access_name]
                for cv in layer_var.coordinates.strip().split():
                    try:
                        coord_var = nc.variables[cv]
                        if hasattr(coord_var, 'axis') and coord_var.axis.lower().strip() == 'z':
                            return coord_var
                        elif hasattr(coord_var, 'positive') and coord_var.positive.lower().strip() in ['up', 'down']:
                            return coord_var
                    except BaseException:
                        pass
            except AttributeError:
                pass

    def _spatial_data_subset(self, data, spatial_index):
        rows = spatial_index[0, :]
        columns = spatial_index[1, :]
        data_subset = data[rows, columns]
        return data_subset

    # same as ugrid
    def depth_direction(self, layer):
        d = self.depth_variable(layer)
        if d is not None:
            if hasattr(d, 'positive'):
                return d.positive
        return 'unknown'

    def depths(self, layer):
        """ sci-wms only deals in depth indexes at this time (no sigma) """
        d = self.depth_variable(layer)
        if d is not None:
            return list(range(0, d.shape[0]))
        return []

    def humanize(self):
        return "SGRID"
