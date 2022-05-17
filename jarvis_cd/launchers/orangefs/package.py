from jarvis_cd.echo_node import EchoNode
from jarvis_cd.exec_node import ExecNode
from jarvis_cd.hostfile import Hostfile
from jarvis_cd.launcher import Launcher, LauncherConfig
import os
import socket

from jarvis_cd.scp_node import SCPNode
from jarvis_cd.sleep_node import SleepNode
from jarvis_cd.ssh_node import SSHNode

class Orangefs(Launcher):
    def __init__(self, config_path=None, args=None):
        super().__init__('orangefs', config_path, args)

    def _LoadConfig(self):
        self.server_data_hosts = Hostfile().LoadHostfile(self.config['SERVER']['SERVER_DATA_HOST_FILE'])
        self.server_meta_hosts = Hostfile().LoadHostfile(self.config['SERVER']['SERVER_META_HOST_FILE'])
        self.client_hosts = Hostfile().LoadHostfile(self.config['CLIENT']['CLIENT_HOST_FILE'])
        self.pvfs_genconfig = os.path.join(self.config["COMMON"]["ORANGEFS_INSTALL_DIR"], "bin", "pvfs2-genconfig")

    def SetNumHosts(self, num_data_hosts, num_meta_hosts, num_client_hosts):
        self.server_data_hosts.SelectHosts(num_data_hosts)
        self.server_meta_hosts.SelectHosts(num_meta_hosts)
        self.client_hosts.SelectHosts(num_client_hosts)
        return

    def _DefineClean(self):
        nodes = []
        nodes.append(SSHNode("clean client data", self.client_hosts[0],
                             "rm -rf {}/*".format(self.config['CLIENT']['CLIENT_MOUNT_POINT_DIR'])))
        return nodes

    def _DefineStatus(self):
        nodes = []
        nodes.append(SSHNode("check clients", self.server_data_hosts, "mount | grep pvfs"))
        pvfs2_ping = os.path.join(self.config['COMMON']['ORANGEFS_INSTALL_DIR'], "bin", "pvfs2-ping")
        verify_server_cmd = "export LD_LIBRARY_PATH={pvfs2_lib}; export PVFS2TAB_FILE={client_pvfs2tab}; " \
                            "{pvfs2_ping} -m {mount_point} | grep 'appears to be correctly configured'".format(
            pvfs2_lib=os.path.join(self.config['COMMON']['ORANGEFS_INSTALL_DIR'], "lib"),
            client_pvfs2tab=self.config['CLIENT']['CLIENT_PVFS2TAB_FILE'],
            pvfs2_ping=pvfs2_ping,
            mount_point=self.config['CLIENT']['CLIENT_MOUNT_POINT_DIR']
        )
        nodes.append(SSHNode("check server", self.client_hosts, verify_server_cmd, print_output=True))
        return nodes

    def _DefineStop(self):
        nodes = []
        for i, client in self.client_hosts.enumerate():
            cmds = [
                "umount -l {mount_point}".format(mount_point=self.config['CLIENT']['CLIENT_MOUNT_POINT_DIR']),
                "umount -f {mount_point}".format(mount_point=self.config['CLIENT']['CLIENT_MOUNT_POINT_DIR']),
                "umount {mount_point}".format(mount_point=self.config['CLIENT']['CLIENT_MOUNT_POINT_DIR']),
                "killall -9 pvfs2-client",
                "killall -9 pvfs2-client-core",
                "rmmod pvfs2",
                "kill-pvfs2-client"
            ]
            node = SSHNode("stop client",client, cmds, sudo=True)
            nodes.append(node)
        nodes.append(SSHNode("stop server",self.server_data_hosts,"killall -9 pvfs2-server"))
        nodes.append(SSHNode("check server", self.client_hosts,"pgrep -la pvfs2-server",print_output=True))
        nodes.append(SSHNode("clean server data",self.server_data_hosts, "rm -rf {}".format(self.config['SERVER']['SERVER_LOCAL_STORAGE_DIR'])))
        return nodes

    def _DefineInit(self):
        nodes = []

        ## create tmp dir
        tmp_dir_node = SSHNode("make tmp dir in clients",self.client_hosts,"mkdir -p {}".format(self.temp_dir))
        nodes.append(tmp_dir_node)
        ## copy pfs conf
        copy_node = SCPNode("cp conf file",self.server_data_hosts,pfs_conf,pfs_conf)
        nodes.append(copy_node)
        server_start_cmds =[
            "rm -rf {}".format(self.config['SERVER']['SERVER_LOCAL_STORAGE_DIR']),
            "{pfs_server} {pfs_conf} -f".format(pfs_server=pvfs2_server, pfs_conf=pfs_conf),
            "{pfs_server} {pfs_conf}".format(pfs_server=pvfs2_server, pfs_conf=pfs_conf)
        ]
        server_start_node = SSHNode("start servers",self.server_data_hosts,server_start_cmds)
        nodes.append(server_start_node)
        nodes.append(SleepNode("sleep timer",5,print_output=True))
        nodes.append(EchoNode("verify server printing","Verifying OrangeFS servers ..."))

        verify_server_cmd = "export LD_LIBRARY_PATH={pvfs2_lib}; export PVFS2TAB_FILE={client_pvfs2tab}; " \
                            "{pvfs2_ping} -m {mount_point} | grep 'appears to be correctly configured'".format(
            pvfs2_lib=os.path.join(self.config['COMMON']['ORANGEFS_INSTALL_DIR'],"lib"),
            client_pvfs2tab=self.config['CLIENT']['CLIENT_PVFS2TAB_FILE'],
            pvfs2_ping=pvfs2_ping,
            mount_point=self.config['CLIENT']['CLIENT_MOUNT_POINT_DIR']
        )
        nodes.append(SSHNode("verify server",self.client_hosts,verify_server_cmd,print_output=True))

        # generate PFS Gen config
        pvfs_gen_cmd = "{binary} --quiet " \
                    "--protocol {protocol} " \
                    "--tcpport {port} " \
                    "--dist-name {dist_name} " \
                    "--dist-params strip_size:{strip_size} "\
                    "--ioservers {data_servers} "\
                    "--metaservers {meta_servers} "\
                    "--storage {data_dir} "\
                    "--metadata {meta_dir} "\
                    "--logfile {log_file} "\
                    "{conf_file}".format(binary=self.pvfs_genconfig,
                                                            protocol=self.config['SERVER']['PVFS2_PROTOCOL'],
                                                            port=self.config['SERVER']['PVFS2_PORT'],
                                                            dist_name=self.config['SERVER']['PVFS2_DISTRIBUTION_NAME'],
                                                            strip_size=self.config['SERVER']['PVFS2_STRIP_SIZE'],
                                                            data_servers=self.server_data_hosts.to_str(sep=','),
                                                            meta_servers=self.server_meta_hosts.to_str(sep=','),
                                                            data_dir=os.path.join(self.config['SERVER']['SERVER_LOCAL_STORAGE_DIR'],"data"),
                                                            meta_dir=os.path.join(self.config['SERVER']['SERVER_LOCAL_STORAGE_DIR'],"meta"),
                                                            log_file=os.path.join(self.config['SERVER']['SERVER_LOCAL_STORAGE_DIR'],"orangefs.log"),
                                                            conf_file=pfs_conf)
        pfs_genconfig_node = ExecNode("generate pfs conf",pvfs_gen_cmd)
        nodes.append(pfs_genconfig_node)

        # set pvfstab on clients
        for i,client in self.client_hosts.enumerate():
            metadata_server = self.server_meta_hosts[i % len(self.server_meta_hosts)]
            metadata_server_ip = socket.gethostbyname(metadata_server)
            cmd = "echo '{protocol}://{ip}:{port}/orangefs {mount_point} pvfs2 defaults,auto 0 0' > {client_pvfs2tab}".format(
                protocol=self.config['SERVER']['PVFS2_PROTOCOL'],
                port=self.config['SERVER']['PVFS2_PORT'],
                ip=metadata_server_ip,
                mount_point=self.config['CLIENT']['CLIENT_MOUNT_POINT_DIR'],
                client_pvfs2tab=self.config['CLIENT']['CLIENT_PVFS2TAB_FILE']
            )
            node = SSHNode("set pvfstab for client {}".format(metadata_server),client,cmd)
            nodes.append(node)

        return nodes

    def _DefineStart(self):
        nodes = []
        pfs_conf = os.path.join(self.temp_dir,"pfs_{}.conf".format(len(self.server_data_hosts)))
        # start pfs servers
        pvfs2_server = os.path.join(self.config['COMMON']['ORANGEFS_INSTALL_DIR'],"sbin","pvfs2-server")
        pvfs2_ping = os.path.join(self.config['COMMON']['ORANGEFS_INSTALL_DIR'],"bin","pvfs2-ping")
        # start pfs client
        kernel_ko = os.path.join(self.config['COMMON']['ORANGEFS_INSTALL_DIR'], "lib/modules/3.10.0-862.el7.x86_64/kernel/fs/pvfs2/pvfs2.ko")
        pvfs2_client = os.path.join(self.config['COMMON']['ORANGEFS_INSTALL_DIR'], "sbin","pvfs2-client")
        pvfs2_client_core = os.path.join(self.config['COMMON']['ORANGEFS_INSTALL_DIR'], "sbin", "pvfs2-client-core")
        for i,client in self.client_hosts.enumerate():
            metadata_server = self.server_meta_hosts[i % len(self.server_meta_hosts)]
            metadata_server_ip = socket.gethostbyname(metadata_server)
            start_client_cmds = [
                "mkdir -p {}".format(self.config['CLIENT']['CLIENT_MOUNT_POINT_DIR']),
                "insmod {}".format(kernel_ko),
                "{} -p {}".format(pvfs2_client, pvfs2_client_core),
                "mount -t pvfs2 {protocol}://{ip}:{port}/orangefs {mount_point}".format(
                    protocol=self.config['SERVER']['PVFS2_PROTOCOL'],
                    port=self.config['SERVER']['PVFS2_PORT'],
                    ip=metadata_server_ip,
                    mount_point=self.config['CLIENT']['CLIENT_MOUNT_POINT_DIR'])
            ]
            node = SSHNode("mount pvfs2 client {}".format(metadata_server),client,start_client_cmds, sudo=True)
            nodes.append(node)
        return nodes
