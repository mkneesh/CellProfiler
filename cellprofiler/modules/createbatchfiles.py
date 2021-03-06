'''<b>Create Batch Files</b> produces files that allow individual batches of images to be processed
separately on a cluster of computers
<hr>

This module creates files that can be submitted in parallel to a
cluster for faster processing. It should be placed at the end of
an image processing pipeline.

If your computer mounts the file system differently than the cluster computers,
<b>CreateBatchFiles</b> can replace the necessary parts of the paths to the 
image and output files. For instance, a Windows machine might 
access files images by mounting the file system using a drive letter, like this:<br><br>
<tt>Z:\imaging_analysis</tt><br><br>
and the cluster computers access the same file system like this:<br><br>
<tt>/imaging/analysis</tt><br><br>
In this case, the local root path is <tt>Z:\imaging_analysis</tt> and the cluster 
root path is <tt>/imaging/analysis</tt>.
'''
# CellProfiler is distributed under the GNU General Public License.
# See the accompanying file LICENSE for details.
# 
# Copyright (c) 2003-2009 Massachusetts Institute of Technology
# Copyright (c) 2009-2012 Broad Institute
# 
# Please see the AUTHORS file for credits.
# 
# Website: http://www.cellprofiler.org

__version__="$Revision$"

import httplib
import numpy as np
import os
import re
import sys
import urllib
import zlib

import cellprofiler.cpimage as cpi
import cellprofiler.cpmodule as cpm
import cellprofiler.measurements as cpmeas
import cellprofiler.pipeline as cpp
import cellprofiler.settings as cps
import cellprofiler.preferences as cpprefs
import cellprofiler.workspace as cpw

'''# of settings aside from the mappings'''
S_FIXED_COUNT = 8
'''# of settings per mapping'''
S_PER_MAPPING = 2

'''Name of the batch data file'''
F_BATCH_DATA = 'Batch_data.mat'

'''Name of the .h5 batch data file'''
F_BATCH_DATA_H5 = 'Batch_data.h5'

class CreateBatchFiles(cpm.CPModule):
    #
    # How it works:
    #
    # There are three hidden settings: batch_mode, pickled_image_set_list, and
    #     distributed_mode
    # batch_mode controls the mode: False means "save the pipeline" and
    #     True means "run the pipeline"
    # pickled_image_set_list holds the state of the image set list. If
    #     batch_mode is False, we save the state of the image set list in
    #     pickled_image_set_list. If batch_mode is True, we load the state
    #     from pickled_image_set_list.
    # distributed_mode indicates whether the pipeline is being
    #     processed by distributed workers, in which case, the default
    #     input and output directories are set to the temporary
    #     directory.
    module_name = "CreateBatchFiles"
    category = 'File Processing'
    variable_revision_number = 6
    
    #
    def create_settings(self):
        '''Create the module settings and name the module'''
        self.wants_default_output_directory = cps.Binary("Store batch files in default output folder?", True,doc="""
                Do you want to store the batch files in the default output folder? 
                Check this box to store batch files in the Default Output folder. Uncheck
                the box to enter the path to the folder that will be used to store
                these files.""")
        
        self.custom_output_directory = cps.Text("Output folder path",
                                                cpprefs.get_default_output_directory(),doc="""
                                                What is the path to the output folder?""")
        
        # Worded this way not because I am windows-centric but because it's
        # easier than listing every other OS in the universe except for VMS
        self.remote_host_is_windows = cps.Binary("Are the cluster computers running Windows?",
                                                 False,doc="""
                Check this box if the cluster computers are running one of the Microsoft
                Windows operating systems. If you check this box, <b>CreateBatchFiles</b> will
                modify all paths to use the Windows file separator (backslash &#92;). If you
                leave the box unchecked, <b>CreateBatchFiles</b> will modify all paths to use
                the Unix or Macintosh file separator (slash,&#47;).""")
        
        self.batch_mode = cps.Binary("Hidden: in batch mode", False)
        self.distributed_mode = cps.Binary("Hidden: in distributed mode", False)
        self.default_image_directory = cps.Setting("Hidden: default input folder at time of save",
                                                   cpprefs.get_default_image_directory())
        self.revision = cps.Integer("Hidden: revision number", 0)
        self.from_old_matlab = cps.Binary("Hidden: from old matlab", False)
        self.acknowledge_old_matlab = cps.DoSomething("Could not update CP1.0 pipeline to be compatible with CP2.0.  See module notes.", "OK",
                                                      self.clear_old_matlab)
        self.mappings = []
        self.add_mapping()
        self.add_mapping_button = cps.DoSomething("", "Add another path mapping",
                                                  self.add_mapping, doc="""
                Use this option if another path must be mapped because there is a difference between how the local computer sees a folder location vs. how the cluster computer sees the folder location.""")
        self.check_path_button = cps.DoSomething(
            "Press this button to check pathnames on the remote server",
            "Check paths", self.check_paths,
            doc = """This button will start a routine that will ask the
            webserver to check whether the default input and default output
            folders exist. It will also check whether all remote
            path mappings exist.""")
    
    def add_mapping(self):
        group = cps.SettingsGroup()
        group.append("local_directory",
                     cps.Text("Local root path",
                                cpprefs.get_default_image_directory(),doc="""
                                What is the path to files on this computer? 
                                This is the root path on the local machine (i.e., the computer setting up
                                the batch files). If <b>CreateBatchFiles</b> finds
                                any pathname that matches the local root path at the begining, it will replace the
                                start with the cluster root path.
                                <p>For example, if you have mapped the remote cluster machine like this:<br><br>
                                <tt>Z:\your_data\images</tt> (on a Windows machine, for instance)<br><br>
                                and the cluster machine sees the same folder like this:<br><br>
                                <tt>/server_name/your_name/your_data/images</tt><br><br>
                                you would enter <tt>Z:</tt> here and <t>/server_name/your_name/</tt> 
                                for the cluster path in the next setting."""))

        group.append("remote_directory",
                     cps.Text("Cluster root path",
                                cpprefs.get_default_image_directory(),doc="""
                                What is the path to files on the cluster? This is the cluster 
                                root path, i.e., how the cluster machine sees the
                                top-most folder where your input/output files are stored.
                                <p>For example, if you have mapped the remote cluster machine like this:<br><br>
                                <tt>Z:\your_data\images</tt> (on a Windows machine, for instance)<br><br>
                                and the cluster machine sees the same folder like this:<br><br>
                                <tt>/server_name/your_name/your_data/images</tt><br><br>
                                you would enter <tt>Z:</tt> in the previous setting for the
                                local machine path and <t>/server_name/your_name/</tt> here. """))
        group.append("remover",
                     cps.RemoveSettingButton("", "Remove this path mapping", self.mappings, group))
        group.append("divider", cps.Divider(line=False))
        self.mappings.append(group)

    def settings(self):
        result = [self.wants_default_output_directory,
                  self.custom_output_directory, self.remote_host_is_windows,
                  self.batch_mode, self.distributed_mode,
                  self.default_image_directory, self.revision, self.from_old_matlab]
        for mapping in self.mappings:
            result += [mapping.local_directory, mapping.remote_directory]
        return result
    
    def prepare_settings(self, setting_values):
        if (len(setting_values) - S_FIXED_COUNT) % S_PER_MAPPING != 0:
            raise ValueError("# of mapping settings (%d) "
                             "is not a multiple of %d" %
                             (len(setting_values) - S_FIXED_COUNT, 
                             S_PER_MAPPING))
        mapping_count = (len(setting_values) - S_FIXED_COUNT) / S_PER_MAPPING
        while mapping_count < len(self.mappings):
            del self.mappings[-1]
        
        while mapping_count > len(self.mappings):
            self.add_mapping()
    
    def visible_settings(self):
        if self.from_old_matlab:
            return [self.acknowledge_old_matlab]
        result = [self.wants_default_output_directory]
        if not self.wants_default_output_directory.value:
            result += [self.custom_output_directory]
        result += [self.remote_host_is_windows]
        for mapping in self.mappings:
            result += mapping.visible_settings()
        result += [self.add_mapping_button, self.check_path_button]
        return result
    
    def prepare_run(self, workspace):
        '''Invoke the image_set_list pickling mechanism and save the pipeline'''
        
        pipeline = workspace.pipeline
        image_set_list = workspace.image_set_list
        frame = workspace.frame
        
        if pipeline.test_mode or self.from_old_matlab:
            return True
        if self.batch_mode.value:
            self.enter_batch_mode(workspace)
            return True
        else:
            self.save_pipeline(workspace)
            return False
    
    def run(self, workspace):
        # all the actual work is done in prepare_run
        pass

    def display(self, workspace):
        if workspace.frame != None:
            if workspace.pipeline.test_mode:
                message = 'In test mode: no batch files created'
            else:
                message = 'Batch files created.'
            figure = workspace.create_or_find_figure(title="CreateBatchFiles, image cycle #%d"%(
                workspace.measurements.image_set_number), subplots=(1,1))
            figure.subplot_table(0, 0, [[message]])

    def is_interactive(self):
        return False

    def clear_old_matlab(self):
        self.from_old_matlab.value = cps.NO

    def validate_module(self, pipeline):
        '''Make sure the module settings are valid'''
        # Ensure we're not an un-updatable version of the module from way back.
        if self.from_old_matlab.value:
            raise cps.ValidationError("The pipeline you loaded was from an old version of CellProfiler 1.0, "
                                      "which could not be made compatible with this version of CellProfiler.",
                                      self.acknowledge_old_matlab)
        # This must be the last module in the pipeline
        if self.module_num != len(pipeline.modules()):
            raise cps.ValidationError("The CreateBatchFiles module must be "
                                      "the last in the pipeline.",
                                      self.wants_default_output_directory)

    def validate_module_warnings(self, pipeline):
        '''Warn user re: Test mode '''
        if pipeline.test_mode:
            raise cps.ValidationError("CreateBatchFiles will not produce output in Test Mode",
                                      self.wants_default_output_directory)
        
    def save_pipeline(self, workspace, outf=None):
        '''Save the pipeline in Batch_data.mat
        
        Save the pickled image_set_list state in a setting and put this
        module in batch mode.

        if outf is not None, it is used as a file object destination.
        '''
        from cellprofiler.utilities.version import version_number

        if outf is None:
            if self.wants_default_output_directory.value:
                path = cpprefs.get_default_output_directory()
            else:
                path = cpprefs.get_absolute_path(self.custom_output_directory.value)
            h5_path = os.path.join(path, F_BATCH_DATA_H5)
        else:
            h5_path = outf
        
        image_set_list = workspace.image_set_list
        pipeline = workspace.pipeline
        m = cpmeas.Measurements(copy = workspace.measurements,
                                filename = h5_path)
        assert isinstance(image_set_list, cpi.ImageSetList)
        assert isinstance(pipeline, cpp.Pipeline)
        assert isinstance(m, cpmeas.Measurements)

        pipeline = pipeline.copy()
        target_workspace = cpw.Workspace(pipeline, None, None, None,
                                         m, image_set_list,
                                         workspace.frame)
        pipeline.prepare_to_create_batch(target_workspace, self.alter_path)
        bizarro_self = pipeline.module(self.module_num)
        bizarro_self.revision.value = version_number
        if self.wants_default_output_directory:
            bizarro_self.custom_output_directory.value = \
                        self.alter_path(cpprefs.get_default_output_directory())
        bizarro_self.default_image_directory.value = \
                    self.alter_path(cpprefs.get_default_image_directory())
        bizarro_self.batch_mode.value = True
        pipeline.write_pipeline_measurement(m)
        del m

    def in_batch_mode(self):
        '''Tell the system whether we are in batch mode on the cluster'''
        return self.batch_mode.value
    
    def enter_batch_mode(self, workspace):
        '''Restore the image set list from its setting as we go into batch mode'''
        image_set_list = workspace.image_set_list
        pipeline = workspace.pipeline
        assert isinstance(image_set_list, cpi.ImageSetList)
        assert isinstance(pipeline, cpp.Pipeline)
        if self.distributed_mode:
            import tempfile
            try:
                cpprefs.set_default_output_directory(tempfile.gettempdir())
                cpprefs.set_default_image_directory(tempfile.gettempdir())
            except:
                pass
        else:
            cpprefs.set_default_output_directory(self.custom_output_directory.value)
            cpprefs.set_default_image_directory(self.default_image_directory.value)
    
    def turn_off_batch_mode(self):
        '''Remove any indications that we are in batch mode
        
        This call restores the module to an editable state.
        '''
        self.batch_mode.value = False
        self.batch_state = np.zeros((0,),np.uint8)
    
    def check_paths(self):
        '''Check to make sure the default directories are remotely accessible'''
        import wx
        
        def check(path):
            more = urllib.urlencode(dict(path=path))
            url = ("/batchprofiler/cgi-bin/development/"
                   "CellProfiler_2.0/PathExists.py?%s") % more
            conn = httplib.HTTPConnection("imageweb")
            conn.request("GET",url)
            result = conn.getresponse()
            if result.status != httplib.OK:
                raise RuntimeError("HTTP failed: %s" % result.reason)
            body = result.read()
            return body.find("OK") != -1
        
        all_ok = True
        for mapping in self.mappings:
            path = mapping.remote_directory.value
            if not check(path):
                wx.MessageBox("Cannot find %s on the server." % path)
                all_ok = False
        for path in (cpprefs.get_default_image_directory(),
                     cpprefs.get_default_output_directory()):
            if not check(self.alter_path(path)):
                wx.MessageBox("Cannot find %s on the server." % path)
                all_ok = False
            
        if all_ok:
            wx.MessageBox("All paths are accessible")
            
    def alter_path(self, path, **varargs):
        '''Modify the path passed so that it can be executed on the remote host
        
        path = path to modify
        regexp_substitution - if true, exclude \g<...> from substitution
        '''
        for mapping in self.mappings:
            if sys.platform.startswith('win'):
                # Windows is case-insentitve so do case-insensitve mapping
                if path.upper().startswith(mapping.local_directory.value.upper()):
                    path = (mapping.remote_directory.value +
                            path[len(mapping.local_directory.value):])
            else:
                if path.startswith(mapping.local_directory.value):
                    path = (mapping.remote_directory.value +
                            path[len(mapping.local_directory.value):])
        if self.remote_host_is_windows.value:
            path = path.replace('/','\\')
        elif (varargs.has_key("regexp_substitution") and
                 varargs["regexp_substitution"]):
            path = re.subn('\\\\\\\\','/',path)[0]
            path = re.subn('\\\\(?!g\\<[^>]*\\>)','/',path)[0]
        else:
            path = path.replace('\\','/')
        return path
        
            
    def upgrade_settings(self, setting_values, variable_revision_number,
                         module_name, from_matlab):
        if from_matlab and variable_revision_number < 8:
            # We never were able to convert from pre-8 to 8 in Matlab.  Why may
            # be lost to history, but my guess is there were conflicting ways
            # to interpret settings previous to this point, so we decided not
            # to try to automate it.
            self.notes = ["The pipeline you loaded was from an old version of CellProfiler 1.0, "
                          "which could not be made compatible with this version of CellProfiler.",
                          "For reference, previous values were:"] + [str(x) for x in setting_values]
            setting_values = [cps.NO,
                              "", cps.NO,
                              cps.NO, cps.NO,
                              "", 0, cps.YES]
            variable_revision_number = 6
            from_matlab = False

        if from_matlab and variable_revision_number == 8:
            batch_save_path, old_pathname, new_pathname = setting_values[:3]
            if batch_save_path == '.':
                wants_default_output_directory = cps.YES
                batch_save_path = cpprefs.get_default_output_directory()
            else:
                wants_default_output_directory = cps.NO
            old_pathnames = old_pathname.split(',')
            new_pathnames = new_pathname.split(',')
            if len(old_pathnames) != len(new_pathnames):
                raise ValueError("Number of pathnames does not match. "
                                 "%d local pathnames, but %d remote pathnames" %
                                 (len(old_pathnames), len(new_pathnames)))
            setting_values = [wants_default_output_directory, batch_save_path,
                              cps.NO, cps.NO, ""]
            for old_pathname, new_pathname in zip(old_pathnames, new_pathnames):
                setting_values += [old_pathname, new_pathname]
            from_matlab = False
            variable_revision_number = 1
        if (not from_matlab) and variable_revision_number == 1:
            setting_values = (setting_values[:5] + 
                              [cpprefs.get_default_image_directory()] +
                              setting_values[5:])
            variable_revision_number = 2
        if (not from_matlab) and variable_revision_number == 2:
            from cellprofiler.utilities.version import version_number
            
            setting_values = (setting_values[:6] + 
                              [version_number] +
                              setting_values[6:])
            variable_revision_number = 3
        if (not from_matlab) and variable_revision_number == 3:
            # Pickled image list is now the batch state
            self.batch_state = np.array(zlib.compress(setting_values[4]))
            setting_values = setting_values[:4]+setting_values[5:]
            variable_revision_number = 4
        if (not from_matlab) and variable_revision_number == 4:
            setting_values = setting_values[:4] + [False] + setting_values[4:]
            variable_revision_number = 5
        if (not from_matlab) and variable_revision_number == 5:
            # added from_old_matlab
            setting_values = setting_values[:7] + [False] + setting_values[7:]
            variable_revision_number = 6
        return setting_values, variable_revision_number, from_matlab
    
