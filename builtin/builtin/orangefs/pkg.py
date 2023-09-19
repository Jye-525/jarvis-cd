from jarvis_cd.basic.pkg import Service
from jarvis_util import *
import os
import time


class Orangefs(Service):
    def _init(self):
        """
        Initialize paths
        """

    def _configure_menu(self):
        return [
            {
                'name': 'port',
                'msg': 'The port to listen for data on',
                'type': int,
                'default': 3334
            },
            {
                'name': 'dev_type',
                'msg': 'The device to spawn orangefs over',
                'type': str,
                'default': None,
            },
            {
                'name': 'stripe_size',
                'msg': 'The stripe size',
                'type': int,
                'default': 65536,
            },
            {
                'name': 'stripe_dist',
                'msg': 'The striping distribution algorithm',
                'type': str,
                'default': 'simple_stripe',
            },
            {
                'name': 'protocol',
                'msg': 'The network protocol (tcp/ib)',
                'type': str,
                'default': 'tcp',
                'choices': ['tcp', 'ib']
            },
            {
                'name': 'mount',
                'msg': 'Where to mount orangefs clients',
                'type': str,
                'default': None,
            },
            {
                'name': 'md_hosts',
                'msg': 'The number of metadata management servers to spawn',
                'type': int,
                'default': None,
            },
            {
                'name': 'name',
                'msg': 'The name of the orangefs installation',
                'type': int,
                'default': 'orangefs',
            },
        ]

    def configure(self, **kwargs):
        """
        Converts the Jarvis configuration to application-specific configuration.
        E.g., OrangeFS produces an orangefs.xml file.

        :param kwargs: Configuration parameters for this pkg.
        :return: None
        """
        self.update_config(kwargs, rebuild=False)
        rg = self.jarvis.resource_graph

        # Configure hosts
        self.md_hosts = self.jarvis.hostfile
        if self.config['md_hosts'] is None:
            count = int(len(self.md_hosts) / 4)
            if count < 1:
                count = 1
            self.md_hosts = self.md_hosts.subset(count)
        else:
            self.md_hosts = self.md_hosts.subset('md_hosts')
        self.client_hosts = self.jarvis.hostfile
        self.server_hosts = self.jarvis.hostfile
        self.config['client_hosts'] = self.client_hosts.hosts
        self.config['server_hosts'] = self.server_hosts.hosts
        self.config['md_hosts'] = self.md_hosts.hosts

        # Locate storage hardware
        dev_df = []
        if self.config['dev_type'] is None:
            dev_types = ['hdd', 'ssd', 'nvme', 'dimm']
            for dev_type in dev_types:
                dev_df = rg.find_storage(dev_types=[dev_type],
                                         shared=False)
                if len(dev_df) != 0:
                    break
        else:
            dev_df = rg.find_storage(dev_types=[self.config['dev_type']],
                                     shared=False)
        if len(dev_df) == 0:
            raise Exception('Could not find any storage devices :(')
        storage_dir = os.path.expandvars(dev_df.rows[0]['mount'])
        print(storage_dir)

        # Define paths
        self.config['pfs_conf'] = f'{self.private_dir}/orangefs.xml'
        self.config['pvfs2tab'] = f'{self.private_dir}/pvfs2tab'
        if self.config['mount'] is None:
            self.config['mount'] = f'{self.private_dir}/client'
        self.config['storage'] = f'{storage_dir}/orangefs_storage'
        self.config['metadata'] = f'{storage_dir}/orangefs_metadata'
        self.config['log'] = f'{self.private_dir}/orangefs_server.log'

        # generate PFS Gen config
        if self.config['protocol'] == 'tcp':
            proto_cmd = f'--tcpport {self.config["port"]}'
        elif self.config['protocol'] == 'ib':
            proto_cmd = f'--ibport {self.config["port"]}'
        else:
            raise Exception("Protocol must be either tcp or ib")
        pvfs_gen_cmd = [
            'pvfs2-genconfig',
            '--quiet',
            f'--protocol {self.config["protocol"]}',
            proto_cmd,
            f'--dist-name {self.config["stripe_dist"]}',
            f'--dist-params \"strip_size: {self.config["stripe_size"]}\"',
            f'--ioservers {self.server_hosts.ip_str(sep=",")}',
            f'--metaservers {self.md_hosts.ip_str(sep=",")}',
            f'--storage {self.config["storage"]}',
            f'--metadata {self.config["metadata"]}',
            f'--logfile {self.config["log"]}',
            f'--fsname {self.config["name"]}',
            self.config['pfs_conf']
        ]
        pvfs_gen_cmd = " ".join(pvfs_gen_cmd)
        print(pvfs_gen_cmd)
        Exec(pvfs_gen_cmd, LocalExecInfo(env=self.env))
        Pscp(self.config['pfs_conf'],
             PsshExecInfo(hosts=self.jarvis.hostfile, env=self.env))

        # Create storage directories
        Mkdir(self.config['mount'],
              PsshExecInfo(hosts=self.client_hosts, env=self.env))
        Mkdir(self.config['storage'], PsshExecInfo(hosts=self.server_hosts,
                                                   env=self.env))
        Mkdir(self.config['metadata'], PsshExecInfo(hosts=self.md_hosts,
                                                    env=self.env))

        # Set pvfstab on clients
        for i, client in self.client_hosts.enumerate():
            metadata_server_ip = self.md_hosts.list()[
                i % len(self.md_hosts)].hosts_ip[0]
            cmd = 'echo "{protocol}://{ip}:{port}/{name} {mount_point} pvfs2 defaults,auto 0 0" > {client_pvfs2tab}'.format(
                protocol=self.config['protocol'],
                port=self.config['port'],
                ip=metadata_server_ip,
                name=self.config['name'],
                mount_point=self.config['mount'],
                client_pvfs2tab=self.config['pvfs2tab'],
            )
            Exec(cmd, SshExecInfo(hosts=client))
        self.env['PVFS2TAB_FILE'] = self.config['pvfs2tab']

        # Initialize servers
        for host in self.server_hosts.list():
            host_ip = host.hosts_ip[0]
            server_start_cmds = [
                f'pvfs2-server {self.config["pfs_conf"]} -f -a {host_ip}',
            ]
            Exec(server_start_cmds, SshExecInfo(
                hosts=host,
                env=self.env))

    def _load_config(self):
        self.client_hosts = Hostfile(all_hosts=self.config['client_hosts'])
        self.server_hosts = Hostfile(all_hosts=self.config['server_hosts'])
        self.md_hosts = Hostfile(all_hosts=self.config['md_hosts'])

    def start(self):
        self._load_config()
        # start pfs servers
        print("Starting the PFS servers")
        for host in self.server_hosts.list():
            host_ip = host.hosts_ip[0]
            server_start_cmds = [
                f'pvfs2-server {self.config["pfs_conf"]} -a {host_ip}'
            ]
            Exec(server_start_cmds, SshExecInfo(
                hosts=host,
                env=self.env))
        time.sleep(5)
        self.status()

        # insert OFS kernel module
        print("Inserting OrangeFS kernel module")
        Exec('modprobe orangefs', PsshExecInfo(sudo=True,
                                               hosts=self.client_hosts,
                                               env=self.env))

        # start pfs client
        print("Starting the OrangeFS clients")
        for i, client in self.client_hosts.enumerate():
            metadata_server_ip = self.md_hosts.list()[
                i % len(self.md_hosts)].hosts_ip[0]
            start_client_cmd = 'mount -t pvfs2 {protocol}://{ip}:{port}/{name} {mount_point}'.format(
                protocol=self.config['protocol'],
                port=self.config['port'],
                ip=metadata_server_ip,
                name=self.config['name'],
                mount_point=self.config['mount'])
            print(start_client_cmd)
            Exec(start_client_cmd, SshExecInfo(
                hosts=client,
                env=self.env,
                sudo=True))

    def stop(self):
        self._load_config()
        cmds = [
            f'umount -l {self.config["mount"]}',
            f'umount -f {self.config["mount"]}',
            f'umount {self.config["mount"]}',
            f'killall -9 pvfs2-client',
            f'killall -9 pvfs2-client-core'
        ]
        Exec(cmds, PsshExecInfo(hosts=self.client_hosts, env=self.env))
        Exec('killall -9 pvfs2-server',
             PsshExecInfo(hosts=self.server_hosts,
                          env=self.env))
        Exec('pgrep -la pvfs2-server',
             PsshExecInfo(hosts=self.client_hosts,
                          env=self.env))

    def clean(self):
        self._load_config()

        Rm(self.config['mount'],
           PsshExecInfo(hosts=self.client_hosts,
                        env=self.env))
        Rm(self.config['storage'],
           PsshExecInfo(hosts=self.server_hosts,
                        env=self.env))
        Rm(self.config['metadata'],
           PsshExecInfo(hosts=self.md_hosts,
                        env=self.env))

    def status(self):
        self._load_config()
        Exec('mount | grep pvfs',
             PsshExecInfo(hosts=self.server_hosts,
                          env=self.env))
        verify_server_cmd = [
            f'pvfs2-ping -m {self.config["mount"]} | grep "appears to be correctly configured"'
        ]
        Exec(verify_server_cmd,
             PsshExecInfo(hosts=self.client_hosts,
                          env=self.env))
        return True
