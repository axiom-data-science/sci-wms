Configure
============

================
Getting Started
================

Refer to this section if you are setting up SCI-WMS for the first time.

***************
The Admin Site
***************

SCI-WMS comes with an administration site built into the server. 
This allows users to add and remove datasets, as well as setup 
and manage groups. (It is essentially the default Django admin 
interface, but it works well for our purposes.)

The admin page can be found on a running instance at **http://server:port/admin** .

The default username is **sciwmsuser**, and the default password is **sciwmspassword**. 
The first thing you should do after getting your server started is to login into the 
administration utility and change the password for this user, or remove the default user and add your 
own.

.. caution::
    Depending on the version of Django you have installed, you 
    may have a problem logging into the admin site, even with 
    the correct password and username. An easy way to solve this 
    problem is to run the following command in a terminal to reset 
    the *sciwmsuser* password using Django's management functionality::
    
        cd sci-wms/src/pywms && python manage.py changepassword sciwmsuser
        
************************
The Openlayers Test Page
************************

The server also comes with a very simple client using *openlayers.js* that can be used for testing 
the GetMap capability of the layers that have been setup in the server. In order for this test page 
to work correctly a number of things must be done. The datasets must be added to the server, the 
dataset *cache* must be initialized (more on this later), and the domain or IP must be added to the 
Sites list in the Admin app.

Add your IP, localhost, or the domain (including ports if applicable) to the Sites list by removing the
default entry and adding your's as a new entry. The "Domain Name" field should be in the following form, omitting "http://" 
and the trailing "/":

    server:port
    
Choose an appropriate nickname for the "Display Name" field.

======================
SCI-WMS Administration
======================

*******************
Adding Datasets
*******************

Django was chosen for the Python web framework because of the out-of-the-box database abstraction API that it provides. There have been some growing pains as Django has matured, and evolved like the change in project/app file structure (we still use the old structure). SCI-WMS uses a sqlite portable file based database that already exists in the project Git repository.

SCI-WMS relies on the database to store dataset metadata, server metadata, and to provide some persistent user-enhanced abstraction with groups and derived layers. 

Datasets in the database
~~~~~~~~~~~~~~~~~~~~~~~~

The **dataset** metadata stored in the database is the following:

 - **Uri**: the opendap endpoint or local file path
 - **Name**: this is really the dataset id, and must be a valid POSIX compliant file name, and lack spaces
 - **Title**:  This shows up in GetCapabilities documents and the like as the human readable Title
 - **Abstract**: This is a human readable abstract for the dataset
 - **Test Layer**: this is the netcdf variable to use as the demo variable in the embedded */wmstest* client included in the app
 - **Test Style**: this is the SCI-WMS style to use on the demo variable in the embedded */wmstest* client included in the app
 - **Keep up to date**: This is a boolean toggle to instruct the server the keep the dataset topology cache up-to-date during cache updates, or treat the file as static and not changeing. (For an operational forecast collection, you would toggle on., For the FVCOM NECOFS 33-yr Hindcast you could just leave it toggled off)
 - **Display all timesteps**: This is a boolean toggle to instruct the service to return the individual timesteps in an dataset for a GetCapabilities request, otherwise just include the range. Returning each timestep number in the GetCapabilities for large NCML OpenDAP aggregations is potentially problematic.

Groups in the database
~~~~~~~~~~~~~~~~~~~~~~

The **groups** elements coordinate which datasets belong to which groups and allow you to provide */wmstest* and *GetCapabilities* for all datasets in a group in single group-endpoints.

Server metadata
~~~~~~~~~~~~~~~

The **server** database element is similarly self explainatory. Edit with specfic metadata about the SCI-WMS instance you are setting up. This information appears in  *GetCapabilities* documents.

Virtual layers
~~~~~~~~~~~~~~

**Virtual Layers** allow you do take a SCI-WMS valid parameter expression using multiple variables from a dataset, and simplify it by using a descriptive layer name that will also appear in the *GetCapabilities* documents. (These will be explained later.) This is so that generic web clients don't necessarility need to know the intracacies of assembling SCI-WMS parameter expressions in requests.

*'/wmstest'* Functionality
~~~~~~~~~~~~~~~~~~~~~~~~~~

In order for the */wmstest* pages to work, there must be a **site** element in the database. One is included by default, but it must be adapted for your particular instance if you want to provide the embedded OpenLayers test client to users. Otherwise layers will appear to be broken in the client. Edit the **Domain Name** in the **site** element with your site and port in the form of `domain_or_ip:port`. Leave off the port if you intend to access the server over port 80. Also leave off the preceeding *http://* and any trailing slashes.

The */wmstest* site can be accessed at `domain_or_ip:port/wmstest` in order to quickly test datasets that have been added to the SCI-WMS instance.

Topology Cache
~~~~~~~~~~~~~~

The topology cache is an important optimization that speeds up response times for datasets that are accessed over opendap. The cache is current located in the folder of the SCI-WMS instance at `path/to/sci-wms/src/pywms`. There are a number of files that make up the cache, and they vary by dataset type.

Each file has a name that is taken directly from the SCI-WMS **Dataset**'s **'Name'**.

Spatial Tree (.idx and .dat)
............................

These files contain serialized *RTree* spatial kd-tree objects that are used for quickly making nearest neighbor queries as part of GetFeatureInfo requests. 

These are necessary for large unstructured meshes, but are also used for the logically rectangular grids as well. (Ideally it would be nice to move away from *RTree* into a better KD-Tree implementation, like *sklearn*'s, that will be have better on disk...will have to accept slower performance when initially buiding the indexes though.)

These files are constructed once when the dataset is added, and then not updated subsequently even if **Keep up to date** is toggled for the dataset.

NetCDF (.nc)
............

This file contains the up-to-date coordinate variable data for the dataset. This is typically Latitude/Longitude, and Time. For forecasts that are routinely updates, the time variable typically is growing with each update.

.. note::
    For unstructured meshes the nodal vertex coordinates of the elements as well of the coodinates of the element centers are stored here.

Bounding Polygon (.domain)
..........................

This file is a Python dump of a Shapely polygon object that represents the maximum-extent bounding polygon of an unstructured mesh dataset.

This file is constructed once when the dataset is added, and then not updated subsequently even if **Keep up to date** is toggled for the dataset.

.. note::
    *This file will only exist for datasets with unstructured meshes*


****************************************
Dataset Cache Initialization & Updating
****************************************

Calls to `server:port/update` will start a process to initialize newly added datasets and update older datasets that have **keep up to date** enabled. The admin interface does not initialize new datasets, it only adds the corresponding metadata into the database.

This initialization and update process can also be started from the command line using the command this command. This what the call to `server:port/update` does in the back end.::

    cd path/to/sci-wms/src/pywms && /path/to/your/python manage.py updatecache

This process builds and updates the proper files outlined in this **Topology Cache** section. **It is probably beneficial to set a cron job that does this routinely every *X* hours on the deployment server.**

This `manage.py updatecache` call actually calls methods in  `grid_init_script.py` (**which is not a script anymore**). This file contains some hacky only vaugly CFish readers to interpret the source file or endpoint and generate the topology cache. Ultimately we should complement the *just sort of OK* readers with the appropriate *perfect CF-UGRID* and GRID readers. This will be more important as full support for ROMS grids is added.




