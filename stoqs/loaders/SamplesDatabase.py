#!/usr/bin/env python

__author__ = "Mike McCann and Olivia Arredondo"
__copyright__ = "Copyright 2015, MBARI"
__license__ = "GPL"
__maintainer__ = "Mike McCann"
__email__ = "mccann at mbari.org"
__status__ = "Development"
__doc__ = '''

The Samples Database contains thousands of re

Olivia Arredondo
MBARI 21 July 2015

@var __date__: Date of last svn commit
@undocumented: __doc__ parser
@author: __author__
@status: __status__
@license: __license__
'''

import os
import sys

# Insert Django App directory (parent of config) into python path 
sys.path.insert(0, os.path.abspath(os.path.join(
                    os.path.dirname(__file__), "../")))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.local'
os.environ['DJANGO_CONFIGURATION'] = 'Local'

# Must install config and setup Django before importing models
from configurations import importer
importer.install()
# django >=1.7
try:
    import django
    django.setup()
except AttributeError:
    pass

from django.contrib.gis.geos import GEOSGeometry, LineString, Point
from django.db.utils import IntegrityError, DatabaseError
from django.db import connection, transaction
from stoqs import models as m
from datetime import datetime, timedelta
from django.core.exceptions import ObjectDoesNotExist
from pydap.client import open_url
import pydap.model
import time
from decimal import Decimal
import math, numpy
from coards import to_udunits, from_udunits, ParserError
import csv
import urllib2
import logging
import socket
import seawater.eos80 as sw
from utils.utils import percentile, median, mode, simplify_points
from loaders import STOQS_Loader, LoadScript, SkipRecord, missing_value, MEASUREDINSITU, FileNotFound
from loaders.DAPloaders import Base_Loader
import numpy as np
from collections import defaultdict
import pymssql

# When settings.DEBUG is True Django will fill up a hash with stats on every insert done to the database.
# "Monkey patch" the CursorWrapper to prevent this.  Otherwise we can't load large amounts of data.
# See http://stackoverflow.com/questions/7768027/turn-off-sql-logging-while-keeping-settings-debug
from django.db.backends import BaseDatabaseWrapper
from django.db.backends.util import CursorWrapper

if settings.DEBUG:
    BaseDatabaseWrapper.make_debug_cursor = lambda self, cursor: CursorWrapper(cursor, self)

# Set up logging
logger = logging.getLogger('__main__')
logger.setLevel(logging.INFO)

rovColors = {   'vnta': 'ff0000',
                'tibr': 'ffff00',
                'docr': 'ff00ff',
            }
