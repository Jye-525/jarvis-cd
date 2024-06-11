"""
This module provides classes and methods to launch Redis.
Redis cluster is used if the hostfile has many hosts
"""
from jarvis_cd.basic.pkg import Application
from jarvis_util import *


class Redis(Application):
    """
    This class provides methods to launch the Ior application.
    """
    def _init(self):
        """
        Initialize paths
        """
        pass

    def _configure_menu(self):
        """
        Create a CLI menu for the configurator method.
        For thorough documentation of these parameters, view:
        https://github.com/scs-lab/jarvis-util/wiki/3.-Argument-Parsing

        :return: List(dict)
        """
        return [
            {
                'name': 'port',
                'msg': 'The port to use for the cluster',
                'type': int,
                'default': 7000,
                'choices': [],
                'args': [],
            },
        ]

    def _configure(self, **kwargs):
        """
        Converts the Jarvis configuration to application-specific configuration.
        E.g., OrangeFS produces an orangefs.xml file.

        :param kwargs: Configuration parameters for this pkg.
        :return: None
        """
        # Create the redis hostfile
        self.copy_template_file(f'{self.pkg_dir}/config/redis.conf',
                                f'{self.shared_dir}/redis.conf')

    def start(self):
        """
        Launch an application. E.g., OrangeFS will launch the servers, clients,
        and metadata services on all necessary pkgs.

        :return: None
        """

        hostfile = self.jarvis.hostfile
        host_str = [f'{host}:{self.config["port"]}' for host in hostfile.hosts]
        host_str = ' '.join(host_str)
        cluster_config_file = f'{self.private_dir}/nodes.conf'
        # Create redis servers
        self.log('Starting individual servers', color=Color.YELLOW)
        cmd = [
            'redis-server',
            f'{self.shared_dir}/redis.conf',
            f'--port {self.config["port"]}',
            f'--appendonly yes',
        ]
        if len(hostfile) > 1:
            cmd += [
                f'--cluster-enabled yes',
                f'--cluster-config-file {cluster_config_file}',
                f'--cluster-node-timeout 5000',
            ]

        cmd = ' '.join(cmd)
        Exec(cmd,
             PsshExecInfo(env=self.mod_env,
                          hostfile=hostfile,
                          do_dbg=self.config['do_dbg'],
                          dbg_port=self.config['dbg_port'],
                          exec_async=True))
        self.log(f'Sleeping for {self.config["sleep"]} seconds', color=Color.YELLOW)
        time.sleep(self.config['sleep'])

        # Create redis clients
        if len(hostfile) > 1:
            self.log('Creating the cluster', color=Color.YELLOW)
            cmd = [
                'redis-cli',
                f'--cluster create {host_str}',
                '--cluster-replicas 1'
            ]
            cmd = ' '.join(cmd)
            print(cmd)
            Exec(cmd,
                 PsshExecInfo(env=self.mod_env,
                              hostfile=hostfile,
                              do_dbg=self.config['do_dbg'],
                              dbg_port=self.config['dbg_port']))

    def stop(self):
        """
        Stop a running application. E.g., OrangeFS will terminate the servers,
        clients, and metadata services.

        :return: None
        """
        Kill('redis-server',
             PsshExecInfo(env=self.env,
                          hostfile=self.jarvis.hostfile))

    def clean(self):
        """
        Destroy all data for an application. E.g., OrangeFS will delete all
        metadata and data directories in addition to the orangefs.xml file.

        :return: None
        """
        Rm(self.config['out'] + '*',
           PsshExecInfo(env=self.env,
                        hostfile=self.jarvis.hostfile))
