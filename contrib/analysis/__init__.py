#!/usr/bin/env python

__author__ = "Mike McCann"
__copyright__ = "Copyright 2013, MBARI"
__license__ = "GPL"
__maintainer__ = "Mike McCann"
__email__ = "mccann at mbari.org"
__status__ = "Development"
__doc__ = '''

Base class for querying the database for measured parameters from the same instantpoint and to
make scatter plots of temporal segments of the data from platforms.

Make use of STOQS metadata to make it as simple as possible to use this script for
different platforms, parameters, and campaigns.

Mike McCann
MBARI January 28, 2014

@var __date__: Date of last svn commit
@undocumented: __doc__ parser
@author: __author__
@status: __status__
@license: __license__
'''

import os
import sys
os.environ['DJANGO_SETTINGS_MODULE']='settings'
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))  # settings.py is one dir up

from django.db import connections
from datetime import datetime, timedelta
from stoqs.models import Activity, ActivityParameter, ParameterResource, Platform, SimpleDepthTime, MeasuredParameter, Parameter
from django.contrib.gis.geos import LineString, Point
from django.db.models import Max, Min
from django.http import HttpRequest
from utils.PQuery import PQuery
from utils.Viz import ParameterParameter, PPDatabaseException
import logging


class NoPPDataException(Exception):
    pass


class NoTSDataException(Exception):
    pass


class BiPlot():
    '''
    Make customized BiPlots (Parameter Parameter plots) from STOQS.
    '''
    def _getAxisInfo(self, platform, parm):
        '''
        Return appropriate min and max values and units for a parameter name
        '''
        # Get the 1 & 99 percentiles of the data for setting limits on the scatter plot
        apQS = ActivityParameter.objects.using(self.args.database).filter(activity__platform__name=platform)
        pQS = apQS.filter(parameter__name=parm).aggregate(Min('p010'), Max('p990'))
        min, max = (pQS['p010__min'], pQS['p990__max'])

        # Get units for each parameter
        prQS = ParameterResource.objects.using(self.args.database).filter(resource__name='units').values_list('resource__value')
        try:
            units = prQS.filter(parameter__name=parm)[0][0]
        except IndexError, e:
            raise Exception("Unable to get units for parameter name %s from platform %s" % (parm, platform))
            sys.exit(-1)

        return min, max, units

    def _getMeasuredPPData(self, startDatetime, endDatetime, platform, xParm, yParm):
        '''
        Use the SQL template to retrieve the X and Y values and other ancillary information from the database
        for the passed in platform, xParm and yParm names.
        '''
        # SQL template copied from STOQS UI Parameter-Parameter -> sql tab
        sql_template = '''SELECT DISTINCT stoqs_measurement.depth,
                mp_x.datavalue AS x, mp_y.datavalue AS y,
                ST_X(stoqs_measurement.geom) AS lon, ST_Y(stoqs_measurement.geom) AS lat,
                stoqs_instantpoint.timevalue 
            FROM stoqs_activity
            INNER JOIN stoqs_platform ON stoqs_platform.id = stoqs_activity.platform_id
            INNER JOIN stoqs_instantpoint ON stoqs_instantpoint.activity_id = stoqs_activity.id
            INNER JOIN stoqs_measurement ON stoqs_measurement.instantpoint_id = stoqs_instantpoint.id
            INNER JOIN stoqs_measurement m_y ON m_y.instantpoint_id = stoqs_instantpoint.id
            INNER JOIN stoqs_measuredparameter mp_y ON mp_y.measurement_id = m_y.id
            INNER JOIN stoqs_parameter p_y ON mp_y.parameter_id = p_y.id
            INNER JOIN stoqs_measurement m_x ON m_x.instantpoint_id = stoqs_instantpoint.id
            INNER JOIN stoqs_measuredparameter mp_x ON mp_x.measurement_id = m_x.id
            INNER JOIN stoqs_parameter p_x ON mp_x.parameter_id = p_x.id
            WHERE (p_x.name = '{pxname}')
                AND (p_y.name = '{pyname}')
                {platform_clause}
                {time_clause}
                {depth_clause}
                {day_night_clause}
            ORDER BY stoqs_instantpoint.timevalue '''

        # Get connection to database; self.args.database must be defined in privateSettings
        cursor = connections[self.args.database].cursor()

        # Apply platform constraint if specified
        platformSQL = ''
        if platform:
            platformSQL += "AND stoqs_platform.name IN ('%s')" % platform

        # Apply time constraints if specified
        timeSQL = ''
        if startDatetime:
            timeSQL += "AND stoqs_instantpoint.timevalue >= '%s' " % startDatetime
        if endDatetime:
            timeSQL += "AND stoqs_instantpoint.timevalue <= '%s'" % endDatetime

        # Apply depth constraints if specified
        depthSQL = ''
        if self.args.minDepth:
            depthSQL += 'AND stoqs_measurement.depth >= %f ' % self.args.minDepth
        if self.args.maxDepth:
            depthSQL += 'AND stoqs_measurement.depth <= %f' % self.args.maxDepth
            
        # Apply SQL where clause to restrict to just do day or night measurements
        daytimeHours = (17, 22)
        nighttimeHours = (5, 10)
        dnSQL = ''
        if self.args.daytime:
            dnSQL = "AND date_part('hour', stoqs_instantpoint.timevalue) > %d AND date_part('hour', stoqs_instantpoint.timevalue) < %d" % daytimeHours
        if self.args.nighttime:
            dnSQL = "AND date_part('hour', stoqs_instantpoint.timevalue) > %d AND date_part('hour', stoqs_instantpoint.timevalue) < %d" % nighttimeHours

        sql = sql_template.format(pxname=xParm, pyname=yParm, platform_clause=platformSQL,
                                    time_clause=timeSQL, depth_clause=depthSQL, day_night_clause=dnSQL)
        if self.args.verbose > 1:
            print "sql =", sql

        x = [] 
        y = []
        points = []
        cursor.execute(sql)
        for row in cursor:
            x.append(float(row[1]))
            y.append(float(row[2]))
            points.append(Point(float(row[3]), float(row[4])))

        if not points:
            raise NoPPDataException("No (%s, %s) data from (%s) between %s and %s" % (xParm, yParm, platform, startDatetime, endDatetime))

        return x, y, points


    def _getPPData(self, startDatetime, endDatetime, platform, xParm, yParm, pvDict={}, returnIDs=False, sampleFlag=True):
        '''
        Get Parameter-Parameter data regardless if Parameters are 'Sampled' or 'Measured in situ'
        '''
        # Use same query builder that the STOQS UI uses
        request = HttpRequest
        request.META = {'dbAlias': self.args.database}
        pq = PQuery(request)
        pq.logger.setLevel(logging.ERROR)
        if self.args.verbose > 2:
            pq.logger.setLevel(logging.DEBUG)

        args = ()
        kwargs = {  'time': (startDatetime.strftime('%Y-%m-%d %H:%M:%S'), endDatetime.strftime('%Y-%m-%d %H:%M:%S')),
                    'platforms': (platform,),
                    'parameterparameter': [ Parameter.objects.using(self.args.database).get(name=xParm).id,
                                            Parameter.objects.using(self.args.database).get(name=yParm).id ],
                    'parametervalues': [pvDict]
                 }

        px, py  = kwargs['parameterparameter']

        pq.buildPQuerySet(*args, **kwargs)

        pp = ParameterParameter(request, {'x': px, 'y': py}, None, pq, {})
        pp.logger.setLevel(logging.ERROR)
        if self.args.verbose > 2:
            pp.logger.setLevel(logging.DEBUG)

        points = []
        try:
            pp._getXYCData(strideFlag=False, latlonFlag=True, returnIDs=returnIDs, sampleFlag=sampleFlag)
        except PPDatabaseException, e:
            if platform or startDatetime or endDatetime:
                raise NoPPDataException("No (%s, %s) data from (%s) between %s and %s" % (xParm, yParm, platform, startDatetime, endDatetime))
            else:
                raise NoPPDataException("No (%s, %s) data returned" % (xParm, yParm))

        for lon, lat in zip(pp.lon, pp.lat):
            points.append(Point(lon, lat))

        if returnIDs:
            return pp.x_id, pp.y_id, pp.x, pp.y, points
        else:
            return pp.x, pp.y, points


    def _getActivityExtent(self, platform=None):
        '''
        Get details of the Activities that the platform(s) ha{s,ve}.  Set those details to member variables and
        also return them as a tuple.  Polymorphic: if platform is a list or tuple return spatial temporal
        extent for all of the platforms.
        '''
        # Get start and end datetimes, color and geographic extent of the activity
        # If multiple platforms use them all to get overall start & end times and extent and se tcolor to black
        if platform:
            if type(platform) in (list, tuple):
                aQS = Activity.objects.using(self.args.database).filter(platform__name__in=platform)
            else:
                aQS = Activity.objects.using(self.args.database).filter(platform__name=platform)
        else:
            aQS = Activity.objects.using(self.args.database).all()

        seaQS = aQS.aggregate(Min('startdate'), Max('enddate'))
        self.activityStartTime = seaQS['startdate__min'] 
        self.activityEndTime = seaQS['enddate__max']

        self.dataExtent = aQS.extent(field_name='maptrack')

        # Expand the computed extent by extendDeg degrees
        if self.args.extend:
            allExtent = self.dataExtent
            extendDeg = self.args.extend
            allExtent = (allExtent[0] - extendDeg, allExtent[1] - extendDeg, allExtent[2] + extendDeg, allExtent[3] + extendDeg)

        # Override with extent if specified on command line
        if self.args.extent:
            allExtent = [float(e) for e in self.args.extent]

        return self.activityStartTime, self.activityEndTime, allExtent

    def _getColor(self, platform):
        '''
        Return color of the platform
        '''
        try:
            colorLookup = {}
            for p,c in zip(self.args.platform, self.args.platformColors):
                colorLookup[p] = c
            self.color = colorLookup[platform]
        except TypeError:
            try:
                self.color = '#' + Platform.objects.using(self.args.database).filter(name=platform).values_list('color')[0][0]
            except IndexError, e:
                raise Exception('Unable to get color of platform name %s' % platform)

        return self.color

    def _getSimpleDepthTime(self, platform):
        '''
        Return a time series of depth for all the activities from specified platform. Separate each activity with a mask
        so that gaps are used when plotted.
        '''
        qsDNTS = Activity.objects.using(self.args.database).filter(platform__name=platform).values_list( 
                                'simpledepthtime__epochmilliseconds', 'simpledepthtime__depth', 'name').order_by('simpledepthtime__epochmilliseconds')

        hash = {}
        for ems, depth, name in qsDNTS:
            try:
                hash[name].append((ems, depth))
            except KeyError:
                hash[name] = []
                hash[name].append((ems, depth))

        return hash

    def _getplatformDTHash(self, platforms):
        '''
        Build hash of depth time series for all the platforms specified.  Useful for making overview plot.
        '''
        hash = {}
        for pl in self.args.platform:
            hash[pl] = self._getSimpleDepthTime(pl)

        return hash

    def _getTimeSeriesData(self, startDatetime, endDatetime, parameterStandardName, platformName=None, parameterName=None):
        '''
        Return time series of a Parameter from a Platform
        '''
        if not (parameterName or parameterStandardName):
            raise Exception('Must specify either parameterName or parameterStandardName')

        qs = MeasuredParameter.objects.using(self.args.database)
        if parameterStandardName:
            qs = qs.filter(parameter__standard_name=parameterStandardName)
        if parameterName:
            qs = qs.filter(parameter__name=parameterName)

        if platformName:
            qs = qs.filter(measurement__instantpoint__activity__platform__name=platformName)
        else:
            count = qs.values_list('measurement__instantpoint__activity__platform').distinct().count()
            if count > 1:
                raise Exception('More that one platform in %s has time series data for parameterStandardName = %s, parameterName = %s' % (
                            self.args.database, parameterStandardName, parameterName))
            elif count == 0:
                raise NoTSDataException('No platform in %s has time series data for parameterStandardName = %s, parameterName = %s' % (
                            self.args.database, parameterStandardName, parameterName))

        qs = qs.values('measurement__instantpoint__timevalue', 'datavalue').order_by('measurement__instantpoint__timevalue')

        tList = []
        dataList = []
        for rs in qs:
            tList.append(rs['measurement__instantpoint__timevalue'])
            dataList.append(rs['datavalue'])

        return tList, dataList

    def _getParametersPlatformHash(self, groupNames=[], ignoreNames=[]):
        '''
        Return a hash of Parameter objects keyed with a set of Platform objects as the values
        '''
        hash = {}
        qs = Parameter.objects.using(self.args.database).all()
        if groupNames:
            qs = qs.filter(parametergroupparameter__parametergroup__name__in=groupNames)
        if ignoreNames:
            qs = qs.exclude(name__in=ignoreNames)
        if self.args.platform:
            qs = qs.filter(activityparameter__activity__platform__name__in=self.args.platform)

        for p in qs:
            hash[p] = set(Platform.objects.using(self.args.database).filter(activity__activityparameter__parameter=p).distinct())

        return hash

