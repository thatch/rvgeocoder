""" Offline Reverse Geocoder in Python

A Python library for offline reverse geocoding. It improves on an existing library
called reverse_geocode developed by Richard Penman & reverse_geocoder by Ajay Thampi.
"""
from __future__ import print_function

import os
import sys
import csv
csv.field_size_limit(sys.maxsize)
import zipfile
from scipy.spatial import cKDTree as KDTree
from rvgeocoder import cKDTree_MP as KDTree_MP
import numpy as np
import io

GN_URL = 'http://download.geonames.org/export/dump/'
GN_CITIES1000 = 'cities1000'
GN_ADMIN1 = 'admin1CodesASCII.txt'
GN_ADMIN2 = 'admin2Codes.txt'

# Schema of the GeoNames Cities with Population > 1000
GN_COLUMNS = {
    'geoNameId': 0,
    'name': 1,
    'asciiName': 2,
    'alternateNames': 3,
    'latitude': 4,
    'longitude': 5,
    'featureClass': 6,
    'featureCode': 7,
    'countryCode': 8,
    'cc2': 9,
    'admin1Code': 10,
    'admin2Code': 11,
    'admin3Code': 12,
    'admin4Code': 13,
    'population': 14,
    'elevation': 15,
    'dem': 16,
    'timezone': 17,
    'modificationDate': 18
}

# Schema of the GeoNames Admin 1/2 Codes
ADMIN_COLUMNS = {
    'concatCodes': 0,
    'name': 1,
    'asciiName': 2,
    'geoNameId': 3
}

# Schema of the cities file created by this library
RG_COLUMNS = [
    'lat',
    'lon',
    'name',
    'admin1',
    'admin2',
    'cc'
]

# Name of cities file created by this library
RG_FILE = 'rg_cities1000.csv'

# WGS-84 major axis in kms
A = 6378.137

# WGS-84 eccentricity squared
E2 = 0.00669437999014

def singleton(cls):
    """
    Function to get single instance of the RGeocoder class
    """
    instances = {}
    def getinstance(**kwargs):
        """
        Creates a new RGeocoder instance if not created already
        """
        if cls not in instances:
            instances[cls] = cls(**kwargs)
        return instances[cls]
    return getinstance


class RGeocoderImpl(object):
    """
    The main reverse geocoder class
    """
    def __init__(self, mode=2, verbose=True, stream=None, stream_columns=None):
        """ Class Instantiation
        Args:`
        mode (int): Library supports the following two modes:
                    - 1 = Single-threaded K-D Tree
                    - 2 = Multi-threaded K-D Tree (Default)
        verbose (bool): For verbose output, set to True
        stream (io.StringIO): An in-memory stream of a custom data source
        """
        self.mode = mode
        self.verbose = verbose
        if stream:
            coordinates, self.locations = self.load(stream, stream_columns)
        else:
            coordinates, self.locations = self.extract(rel_path(RG_FILE))

        if mode == 1: # Single-process
            self.tree = KDTree(coordinates)
        else: # Multi-process
            self.tree = KDTree_MP.cKDTree_MP(coordinates)

    @classmethod
    def from_data(cls, data):
        return cls(stream=io.StringIO(data))

    @classmethod
    def from_files(cls, locations):
        data_stream = cls.load_data(locations)
        return cls(stream=data_stream)
    
    @staticmethod
    def load_data(locations):
        data_stream = io.StringIO()
        if not locations:
            return None
        header_saved = False
        for loc in locations:
            with open(loc) as f:
                header = next(f)
                if not header_saved:
                    data_stream.write(header)
                    header_saved = True
                data_stream.writelines(f.readlines())

        data_stream.seek(0)
        return data_stream


    def query(self, coordinates):
        """
        Function to query the K-D tree to find the nearest city
        Args:
        coordinates (list): List of tuple coordinates, i.e. [(latitude, longitude)]
        """
        if self.mode == 1:
            _, indices = self.tree.query(coordinates, k=1)
        else:
            _, indices = self.tree.pquery(coordinates, k=1)
        return [self.locations[index] for index in indices]

    def query_dist(self, coordinates):
        """
        Function to query the K-D tree to find the nearest city
        Args:
        coordinates (list): List of tuple coordinates, i.e. [(latitude, longitude)]
        """
        if self.mode == 1:
            dists, indices = self.tree.query(coordinates, k=1)
        else:
            dists, indices = self.tree.pquery(coordinates, k=1)
            # in pquery dists returns a list of arrays so get the first element instead of returning array
            dists = [dist[0] for dist in dists]
        return [(dists[n], self.locations[index]) for (n, index) in enumerate(indices)]

    def load(self, stream, stream_columns):
        """
        Function that loads a custom data source
        Args:
        stream (io.StringIO): An in-memory stream of a custom data source.
                              The format of the stream must be a comma-separated file.
        """
        print('Loading geocoded stream ...')
        stream_reader = csv.DictReader(stream, delimiter=',')
        header = stream_reader.fieldnames

        if stream_columns and header != stream_columns:
            raise csv.Error('Input must be a comma-separated file with header containing ' + \
                'the following columns - %s.\nFound header - %s.\nFor more help, visit: ' % (','.join(stream_columns), ','.join(header)) + \
                'https://github.com/thampiman/reverse-geocoder')

        # Load all the coordinates and locations
        geo_coords, locations = [], []
        for row in stream_reader:
            geo_coords.append((row['lat'], row['lon']))
            locations.append(row)

        return geo_coords, locations

    def extract(self, local_filename):
        """
        Function loads the already extracted GeoNames cities file or downloads and extracts it if
        it doesn't exist locally
        Args:
        local_filename (str): Path to local RG_FILE
        """
        if os.path.exists(local_filename):
            if self.verbose:
                print('Loading formatted geocoded file ...')
            rows = csv.DictReader(open(local_filename, 'rt'))
        else:
            rows = self.do_extract(GN_CITIES1000, local_filename)
        
        # Load all the coordinates and locations
        geo_coords, locations = [], []
        for row in rows:
            geo_coords.append((row['lat'], row['lon']))
            locations.append(row)
        return geo_coords, locations

    def do_extract(self, geoname_file, local_filename):
        gn_cities_url = GN_URL + geoname_file + '.zip'
        gn_admin1_url = GN_URL + GN_ADMIN1
        gn_admin2_url = GN_URL + GN_ADMIN2

        cities_zipfilename = geoname_file + '.zip'
        cities_filename = geoname_file + '.txt'

        if not os.path.exists(cities_zipfilename):
            if self.verbose:
                print('Downloading files from Geoname...')
            
            import urllib.request
            urllib.request.urlretrieve(gn_cities_url, cities_zipfilename)
            urllib.request.urlretrieve(gn_admin1_url, GN_ADMIN1)
            urllib.request.urlretrieve(gn_admin2_url, GN_ADMIN2)

        if self.verbose:
            print('Extracting %s...' % geoname_file)
        _z = zipfile.ZipFile(open(cities_zipfilename, 'rb'))
        open(cities_filename, 'wb').write(_z.read(cities_filename))

        if self.verbose:
            print('Loading admin1 codes...')
        admin1_map = {}
        t_rows = csv.reader(open(GN_ADMIN1, 'rt'), delimiter='\t')
        for row in t_rows:
            admin1_map[row[ADMIN_COLUMNS['concatCodes']]] = row[ADMIN_COLUMNS['asciiName']]

        if self.verbose:
            print('Loading admin2 codes...')
        admin2_map = {}
        for row in csv.reader(open(GN_ADMIN2, 'rt'), delimiter='\t'):
            admin2_map[row[ADMIN_COLUMNS['concatCodes']]] = row[ADMIN_COLUMNS['asciiName']]

        if self.verbose:
            print('Creating formatted geocoded file...')
        writer = csv.DictWriter(open(local_filename, 'wt'), fieldnames=RG_COLUMNS)
        rows = []
        for row in csv.reader(open(cities_filename, 'rt'), \
                delimiter='\t', quoting=csv.QUOTE_NONE):
            lat = row[GN_COLUMNS['latitude']]
            lon = row[GN_COLUMNS['longitude']]
            name = row[GN_COLUMNS['asciiName']]
            cc = row[GN_COLUMNS['countryCode']]

            admin1_c = row[GN_COLUMNS['admin1Code']]
            admin2_c = row[GN_COLUMNS['admin2Code']]

            cc_admin1 = cc+'.'+admin1_c
            cc_admin2 = cc+'.'+admin1_c+'.'+admin2_c

            admin1 = ''
            admin2 = ''

            if cc_admin1 in admin1_map:
                admin1 = admin1_map[cc_admin1]
            if cc_admin2 in admin2_map:
                admin2 = admin2_map[cc_admin2]

            write_row = {'lat':lat,
                            'lon':lon,
                            'name':name,
                            'admin1':admin1,
                            'admin2':admin2,
                            'cc':cc}
            rows.append(write_row)
        writer.writeheader()
        writer.writerows(rows)

        if self.verbose:
            print('Removing extracted %s to save space...' % geoname_file)
        os.remove(cities_filename)

        return rows


@singleton
class RGeocoder(RGeocoderImpl):
    pass


def geodetic_in_ecef(geo_coords):
    geo_coords = np.asarray(geo_coords).astype(np.float)
    lat = geo_coords[:, 0]
    lon = geo_coords[:, 1]

    lat_r = np.radians(lat)
    lon_r = np.radians(lon)
    normal = A / (np.sqrt(1 - E2 * (np.sin(lat_r) ** 2)))

    x = normal * np.cos(lat_r) * np.cos(lon_r)
    y = normal * np.cos(lat_r) * np.sin(lon_r)
    z = normal * (1 - E2) * np.sin(lat)

    return np.column_stack([x, y, z])

def rel_path(filename):
    """
    Function that gets relative path to the filename
    """
    return os.path.join(os.getcwd(), os.path.dirname(__file__), filename)

def get(geo_coord, mode=2, verbose=True):
    """
    Function to query for a single coordinate
    """
    if not isinstance(geo_coord, tuple) or not isinstance(geo_coord[0], float):
        raise TypeError('Expecting a tuple')

    _rg = RGeocoder(mode=mode, verbose=verbose)
    return _rg.query([geo_coord])[0]

def search(geo_coords, mode=2, verbose=True):
    """
    Function to query for a list of coordinates
    """
    if not isinstance(geo_coords, tuple) and not isinstance(geo_coords, list):
        raise TypeError('Expecting a tuple or a tuple/list of tuples')
    elif not isinstance(geo_coords[0], tuple):
        geo_coords = [geo_coords]

    _rg = RGeocoder(mode=mode, verbose=verbose)
    return _rg.query(geo_coords)


if __name__ == '__main__':
    print('Testing single coordinate through get...')
    city = (37.78674, -122.39222)
    print('Reverse geocoding 1 city...')
    result = get(city)
    print(result)

    print('Testing coordinates...')
    cities = [(41.852968, -87.725730), (48.836364, 2.357422)]
    print('Reverse geocoding %d locations ...' % len(cities))
    results = search(cities)
    print(results)