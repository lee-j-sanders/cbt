"""
    lirbdfio.py -- module to support the FIO benchmark exercising RBD.
"""
import os
import time
import logging
import pprint
import common
import settings
import monitoring

from .benchmark import Benchmark

logger = logging.getLogger("cbt")

class LibrbdFio(Benchmark):
    """
    Class LibrbdFio
    """

    def __init__(self, archive_dir, cluster, config):
        super(LibrbdFio, self).__init__(archive_dir, cluster, config)

        # FIXME there are too many permutations, need to put results in SQLITE3
        self.cmd_path = config.get('cmd_path', '/usr/bin/fio')
        self.pool_profile = config.get('pool_profile', 'default')
        self.recov_pool_profile = config.get('recov_pool_profile', 'default')
        self.recov_test_type = config.get('recov_test_type', 'blocking')
        self.data_pool_profile = config.get('data_pool_profile', None)
        self.time = config.get('time', None)
        self.precond_time = config.get('precond_time',None )
        # Global FIO options can be overwritten for specific workload options
        # would be nice to have them as a separate class -- future PR
        self.time_based = bool(config.get('time_based', False))
        self.ramp = config.get('ramp', None)
        self.iodepth = config.get('iodepth', 16)
        self.numjobs = config.get('numjobs', 1)
        self.end_fsync = config.get('end_fsync', 0)
        self.mode = config.get('mode', 'write')
        self.rwmixread = config.get('rwmixread', 50)
        self.rwmixwrite = 100 - self.rwmixread
        self.log_avg_msec = config.get('log_avg_msec', None)
        self.op_size = config.get('op_size', 4194304)

        self.pgs = config.get('pgs', 2048)
        self.vol_size = config.get('vol_size', 65536)
        self.vol_object_size = config.get('vol_object_size', 22)
        self.volumes_per_client = config.get('volumes_per_client', 1)
        self.procs_per_volume = config.get('procs_per_volume', 1)
        self.random_distribution = config.get('random_distribution', None)
        self.rate_iops = config.get('rate_iops', None)
        self.fio_out_format = config.get('fio_out_format', 'json,normal')
        self.data_pool = None
        # use_existing_volumes needs to be true to set the pool and rbd names
        self.use_existing_volumes = bool(config.get('use_existing_volumes', False))
        self.no_sudo = bool(config.get('no_sudo', False))
        self.idle_monitor_sleep = config.get('idle_monitor_sleep', 60)
        self.pool_name = config.get("poolname", "cbt-librbdfio")
        self.recov_pool_name = config.get("recov_pool_name", "cbt-librbdfio-recov")
        self.rbdname = config.get('rbdname', '')
        # workloads: specify a list of tests
        self.global_fio_options = {}
        self.workloads = config.get('workloads', {})
        if self.workloads:
            self.backup_global_fio_options()
        self.prefill_vols = config.get('prefill', {'blocksize': '4M',
                                              'numjobs': '1'})

        self.total_procs =  (self.procs_per_volume * self.volumes_per_client *
                             len(settings.getnodes('clients').split(',')))
        self.base_run_dir = self.run_dir # we need this for the new workloads block
        self.run_dir =  f'{self.base_run_dir}/'
        if self.osd_ra is not None:
            self.run_dir += f'osd_ra-{int(self.osd_ra):08d}/'
        self.run_dir +=  ( f'op_size-{int(self.op_size):08d}/'
                        f'concurrent_procs-{int(self.total_procs):03d}/'
                        f'iodepth-{int(self.iodepth):03d}/{self.mode}' )

        self.out_dir = self.archive_dir

        self.norandommap = config.get("norandommap", False)
        self.wait_pgautoscaler_timeout = config.get("wait_pgautoscaler_timeout", -1)
        # Make the file names string (repeated across volumes)
        self.names = ''
        for proc_num in range(self.procs_per_volume):
            rbd_name = f'cbt-librbdfio-`{common.get_fqdn_cmd()}`-file-{proc_num:d}'
            self.names += f'--name={rbd_name} '


    def backup_global_fio_options(self):
        """
        Backup/copy the FIO global options into a dictionary
        """
        self.global_fio_options['time_based'] = self.time_based
        self.global_fio_options['ramp'] = self.ramp
        self.global_fio_options['iodepth'] = self.iodepth
        self.global_fio_options['numjobs'] = self.numjobs
        self.global_fio_options['mode'] = self.mode
        self.global_fio_options['end_fsync'] = self.end_fsync
        self.global_fio_options['rwmixread'] = self.rwmixread
        self.global_fio_options['rwmixwrite'] = self.rwmixwrite
        self.global_fio_options['log_avg_msec'] = self.log_avg_msec
        self.global_fio_options['op_size'] = self.op_size


    def restore_global_fio_options(self):
        """
        Restore the global values that are set before each workload
        """
        self.ramp = self.global_fio_options['ramp']
        self.iodepth = self.global_fio_options['iodepth']
        self.numjobs = self.global_fio_options['numjobs']
        self.mode = self.global_fio_options['mode']
        self.end_fsync = self.global_fio_options['end_fsync']
        self.rwmixread = self.global_fio_options['rwmixread']
        self.rwmixwrite = self.global_fio_options['rwmixwrite']
        self.log_avg_msec = self.global_fio_options['log_avg_msec']
        self.op_size = self.global_fio_options['op_size']
        self.time_based = self.global_fio_options['time_based']


    def exists(self):
        """
        Verify whether the out_dir exists
        """
        if os.path.exists(self.out_dir):
            logger.info('Skipping existing test in %s.', self.out_dir)
            return True
        return False


    def initialize(self):
        super(LibrbdFio, self).initialize()
        # Clean and Create the run directory
        common.clean_remote_dir(self.run_dir)
        common.make_remote_dir(self.run_dir)
        logger.info('Pausing for %ds for idle monitoring.', self.idle_monitor_sleep)
        monitoring.start( f"{self.run_dir}/idle_monitoring" )
        time.sleep(self.idle_monitor_sleep)
        monitoring.stop()
        common.sync_files( f'{self.run_dir}/*', self.out_dir)
        # Create the recovery image based on test type requested
        if 'recovery_test' in self.cluster.config and self.recov_test_type == 'background':
            self.mkrecovimage()
        if self.workloads:
            logger.info(" %d Workloads:\n    %s", len(self.workloads.keys()),
                        pprint.pformat(self.workloads).replace("\n", "\n    "))
        logger.info('Creating fio images...')
        self.mkimages()
        logger.info('Attempting to prefill fio images...')
        # LEE disabled self.prefill()


    def run_workloads(self):
        """
        Main loop for executing workloads
        """
        for wk in self.workloads:
            ps = []
            # aggregate/overwrite the global options
            test = dict(self.global_fio_options, **self.workloads[wk])
            enable_monitor = True
            logger.info('Running rbd fio %s test, mode %s', wk, test['mode'])
            if 'monitor' in test:
                enable_monitor = bool(test['monitor'])
            # TODO: simplify this loop to have a single iterator for general queu depth

            #print type( test['iodepth'] )

            # for iod in test['iodepth']:
            #    d = test['iodepth']
            #    print( iod )
            #    print( type( iod ) )
            #    print( d.keys().index[d] ) 

            # for job in test['numjobs']:
            # for iod in test['iodepth']:

            if 'precond' in test:
               fioruntime = self.precond_time
            else:
               fioruntime = self.time 
  
            for i in range(len(test['iodepth'])):

                iod = test['iodepth'][i]
                job = test['numjobs'][i]

                self.mode = test['mode']
                if 'op_size' in test:
                    self.op_size = test['op_size']

                # LEE
                if 'rwmixread' in test:
                    self.rwmixread = test['rwmixread'] 
                    self.rwmixwrite = 100 - self.rwmixread 

                self.mode = test['mode']
                self.numjobs = job
                self.iodepth = iod

                #LEE
                self.run_dir =  ( f'{self.base_run_dir}/{wk}_{self.mode}_{int(self.op_size)}/'
                                 f'iodepth-{int(self.iodepth):03d}/numjobs-{int(self.numjobs):03d}' )
                common.make_remote_dir(self.run_dir)

                for i in range(self.volumes_per_client):
                    fio_cmd = self.mkfiocmd(i, fioruntime )
                    p = common.pdsh(settings.getnodes('clients'), fio_cmd)
                    ps.append(p)
                if enable_monitor:
                    time.sleep(self.ramp) # ramp up time before measuring
                    monitoring.start(self.run_dir)
                for p in ps:
                    p.wait()
                if enable_monitor:
                    monitoring.stop(self.run_dir)
                self.restore_global_fio_options()

        logger.info('== Workloads completed ==')


    def run(self):
        super(LibrbdFio, self).run()
        # We'll always drop caches for rados bench
        self.dropcaches()
        # Create the run directory
        common.make_remote_dir(self.run_dir)
        # dump the cluster config
        self.cluster.dump_config(self.run_dir)
        time.sleep(5)
        # If the pg autoscaler kicks in before starting the test,
        # wait for it to complete. Otherwise, results may be skewed.
        ret = self.cluster.check_pg_autoscaler(self.wait_pgautoscaler_timeout,
                                               f"{self.run_dir}/pgautoscaler.log")
        if ret == 1:
            logger.warn("PG autoscaler taking longer to complete."
                        "Continuing anyway...results may be skewed.")
        # Start the recovery thread if requested
        if 'recovery_test' in self.cluster.config:
            if self.recov_test_type == 'blocking':
                recovery_callback = self.recovery_callback_blocking
            elif self.recov_test_type == 'background':
                recovery_callback = self.recovery_callback_background
            self.cluster.create_recovery_test(self.run_dir, recovery_callback, self.recov_test_type)

        if 'recovery_test' in self.cluster.config and self.recov_test_type == 'background':
            # Wait for a signal from the recovery thread to initiate client IO
            self.cluster.wait_start_io()

        if len(self.workloads) > 0:
            # New style: execute the list of workloads
            self.run_workloads()
        else:
            # Original style
            monitoring.start(self.run_dir)
            logger.info('Running rbd fio %s test.', self.mode)
            ps = []
            for i in range(self.volumes_per_client):
                fio_cmd = self.mkfiocmd(i,self.time )
                p = common.pdsh(settings.getnodes('clients'), fio_cmd)
                ps.append(p)
            for p in ps:
                p.wait()
        # If we were doing recovery, wait until it's done.
        if 'recovery_test' in self.cluster.config:
            self.cluster.wait_recovery_done()

        monitoring.stop(self.run_dir)

        # Finally, get the historic ops
        self.cluster.dump_historic_ops(self.run_dir)
        common.sync_files(f'{self.run_dir}/*', self.out_dir)
        self.analyze(self.out_dir)


    def mkfiocmd(self, volnum, time):
        """
        Construct a FIO cmd (note the shell interpolation for the host
        executing FIO).
        """
        if self.use_existing_volumes and len(self.rbdname):
            rbdname = self.rbdname
        else:
            rbdname = f'cbt-librbdfio-`{common.get_fqdn_cmd()}`-{volnum:d}'

        logger.debug('Using rbdname %s', rbdname)
        out_file = f'{self.run_dir}/output.{volnum:d}'

        fio_cmd = ''
        if not self.no_sudo:
            fio_cmd = 'sudo '
        fio_cmd += '%s --ioengine=rbd --clientname=admin --pool=%s --rbdname=%s --invalidate=0' % (self.cmd_path, self.pool_name, rbdname)
        fio_cmd += ' --rw=%s' % self.mode
        fio_cmd += ' --output-format=%s' % self.fio_out_format
        if (self.mode == 'readwrite' or self.mode == 'randrw'):
            fio_cmd += ' --rwmixread=%s --rwmixwrite=%s' % (self.rwmixread, self.rwmixwrite)
        if self.time is not None:
            fio_cmd += ' --runtime=%d' % time
        if self.time_based is True:
            fio_cmd += ' --time_based'
        if self.ramp is not None:
            fio_cmd += ' --ramp_time=%d' % self.ramp
        fio_cmd += ' --numjobs=%s' % self.numjobs
        fio_cmd += ' --direct=1'
        fio_cmd += ' --bs=%dB' % self.op_size
        fio_cmd += ' --iodepth=%d' % self.iodepth
        fio_cmd += ' --end_fsync=%d' % self.end_fsync
#        if self.vol_size:
#            fio_cmd += ' -- size=%dM' % self.vol_size
        if self.norandommap:
            fio_cmd += ' --norandommap'
        if self.log_iops:
            fio_cmd += ' --write_iops_log=%s' % out_file
        if self.log_bw:
            fio_cmd += ' --write_bw_log=%s' % out_file
        if self.log_lat:
            fio_cmd += ' --write_lat_log=%s' % out_file
        if 'recovery_test' in self.cluster.config:
            fio_cmd += ' --time_based'
        if self.random_distribution is not None:
            fio_cmd += ' --random_distribution=%s' % self.random_distribution
        if self.log_avg_msec is not None:
            fio_cmd += ' --log_avg_msec=%s' % self.log_avg_msec
        if self.rate_iops is not None:
            fio_cmd += ' --rate_iops=%s' % self.rate_iops

        # End the fio_cmd
        fio_cmd += ' %s > %s' % (self.names, out_file)
        return fio_cmd


    def mkrecovimage(self):
        """
        Create a reecovery image
        """
        logger.info('Creating recovery image...')
        monitoring.start( f"{self.run_dir}/recovery_pool_monitoring" )
        if self.use_existing_volumes is False:
            self.cluster.rmpool(self.recov_pool_name, self.recov_pool_profile)
            self.cluster.mkpool(self.recov_pool_name, self.recov_pool_profile, 'rbd')
            for node in common.get_fqdn_list('clients'):
                for volnum in range(0, self.volumes_per_client):
                    node = node.rpartition("@")[2]
                    self.cluster.mkimage( f'cbt-librbdfio-recov-{node}-{volnum:d}',
                                         self.vol_size, self.recov_pool_name, self.data_pool,
                                         self.vol_object_size )
        monitoring.stop()


    def mkimages(self):
        """
        Create an RBD pool and a number of volumes per client
        """
        monitoring.start( f"{self.run_dir}/pool_monitoring" )
        if self.use_existing_volumes is False:
            self.cluster.rmpool(self.pool_name, self.pool_profile)
            self.cluster.mkpool(self.pool_name, self.pool_profile, 'rbd')
            if self.data_pool_profile:
                self.data_pool = self.pool_name + "-data"
                self.cluster.rmpool(self.data_pool, self.data_pool_profile)
                self.cluster.mkpool(self.data_pool, self.data_pool_profile, 'rbd')

            # LEE - was outdented to always run
            for node in common.get_fqdn_list('clients'):
                for volnum in range(0, self.volumes_per_client):
                    node = node.rpartition("@")[2]
                    self.cluster.mkimage( f'cbt-librbdfio-{node}-{volnum:d}',
                                          self.vol_size, self.pool_name, self.data_pool,
                                          self.vol_object_size)
        monitoring.stop()


    def prefill(self):
        """
        Execute a FIO cmd to prefill the volumes
        """
        logger.info("Prefilling volumes") 
         
        ps = []
        # LEE - was not self.use_existing_volumes
        if self.use_existing_volumes:
            for volnum in range(self.volumes_per_client):
                rbd_name = f'cbt-librbdfio-`{common.get_fqdn_cmd()}`-{volnum:d}'
                pre_cmd = ''
                if not self.no_sudo:
                    pre_cmd += 'sudo '
                numjobs = self.prefill_vols['numjobs']
                bs = self.prefill_vols['blocksize']
                pre_cmd += ( f'{self.cmd_path} --ioengine=rbd --clientname=admin'
                            f' --pool={self.pool_name}'
                            f' --rbdname={rbd_name} --invalidate=0  --rw=write'
                            f' --numjobs={numjobs}'
                            f' --bs={bs}'
                            f' --size {self.vol_size:d}M {self.names}'
                            f' --output-format={self.fio_out_format} > /dev/null' )
                p = common.pdsh(settings.getnodes('clients'), pre_cmd)
                ps.append(p)
            for p in ps:
                p.wait()


    def recovery_callback_blocking(self):
        common.pdsh(settings.getnodes('clients'), 'sudo killall -2 fio').communicate()


    def recovery_callback_background(self):
        logger.info('Recovery thread completed!')


    def parse(self, out_dir):
        """
        Filters the JSON output from the mix output and writes it to a
        separate file.
        """
        for client in settings.getnodes('clients').split(','):
            host = settings.host_info(client)["host"]
            for i in range(self.volumes_per_client):
                found = 0
                out_file = f'{out_dir}/output.{i:d}.{host}'
                json_out_file = f'{out_dir}/json_output.{i:d}.{host}'
                with open(out_file) as fd:
                    with open(json_out_file, 'w') as json_fd:
                        for line in fd.readlines():
                            if len(line.strip()) == 0:
                                found = 0
                                break
                            if found == 1:
                                json_fd.write(line)
                            if found == 0:
                                if "Starting" in line:
                                    found = 1


    def analyze(self, out_dir):
        logger.info('Convert results to json format.')
        self.parse(out_dir)


    def __str__(self):
        return "%s\n%s\n%s" % (self.run_dir, self.out_dir, super(LibrbdFio, self).__str__())
