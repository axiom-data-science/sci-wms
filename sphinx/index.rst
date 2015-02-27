.. SCI-WMS documentation master file, created by
   sphinx-quickstart on Tue Jun  4 13:12:58 2013.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to SCI-WMS
===================================

SCI-WMS is a Python WMS service for geospatial 
gridded data. It supports triangular unstructured meshes and logically 
rectangular grids including grids referred to as *curvilinear*.

If you would like to know if your dataset can be served with SCI-WMS, 
or how to get started with your own SCI-WMS server we would be happy 
to help.


.. toctree::
   :maxdepth: 3

   installation
   deployment
   configure
   using
   roadmap

==========
Background
==========

* No tools for comparing multiple unstructured models out of the box
* WMS technologies don't support unstructured meshes in a format the preserves topology
* Consensus building around a UGRID-CF proposal conventions
* Vast amount of large collections of met-ocean data available over OpenDAP

========
Features
========

* Syles that preserve unstructured mesh topology
* Sophisticated layer specification and style syntax
* Closely linked to matplotlib the predominant Python library for scientific plotting
* Support for groups of datasets
* Topologically correct GetFeatureInfo querying on unstructured meshes and regular grids


=========
Authors
=========
.. codeauthor:: Alex Crosby <https://github.com/acrosby>
.. codeauthor:: Brandon Mayer <https://github.com/brandonmayer>
.. codeauthor:: Brian McKenna <https://github.com/brianmckenna >
.. codeauthor:: Dave Foster <https://github.com/daf>
.. codeauthor:: Kyle Wilcox <https://github.com/kwilcox>
.. codeauthor:: Andrew Yan <https://github.com/ayan-usgs>
