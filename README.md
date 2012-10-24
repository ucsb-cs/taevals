__Note__: This information is out of date

###Backup and Restore To Website
Run one of the following ([source](http://code.google.com/appengine/docs/python/tools/uploadingdata.html#Downloading_and_Uploading_All_Data)):

    appcfg.py download_data --url=http://taevals.appspot.com/remote_api --filename=taevals_`date`_backup.sql3

    appcfg.py upload_data --application=taevals --filename=<data-filename>


###Restore to Localhost
In order to authenticate to the local database, you need to first start the server as follows:

    dev_appserver.py --clear_datastore . --default_partition=""

Then login as the administrator with some address in order to create a
temporary admin account. You can do this by visiting
http://localhost:8080/admin/ and then run:

    appcfg.py upload_data --application=taevals --url=http://localhost:8080/remote_api --filename=<data-filename>
