import pdb
import re
from netaddr import IPNetwork
import ipaddr
import sys
import string
import argparse
from tcutils.cfgparser import parse_cfg_file
from common.contrail_test_init import ContrailTestInit
from common.connections import ContrailConnections
import  yaml
from config import *
from config import Project as ConfigProject
import os
import time

from tcutils.db import TestDB

from vnc_api.vnc_api import *
import datetime

from keystoneclient.auth.identity import v2
from keystoneclient import session
from novaclient import exceptions as nova_exceptions
from keystoneclient.v2_0 import client as kclient
from novaclient import client as nova_client
from neutronclient.neutron import client as neutron_client

from multiprocessing import Process, Queue
import lib
import threading

OS_USERNAME    = os.environ['OS_USERNAME']
OS_PASSWORD    = os.environ['OS_PASSWORD']
OS_TENANT_NAME = os.environ['OS_TENANT_NAME']
OS_AUTH_URL    = os.environ['OS_AUTH_URL']

def parse_yaml_cfg_file(conf_file):
  
   fp = open(conf_file,"r")
   conf = yaml.load(fp)

   return conf

lock = threading.Lock()

class CIDR:

  def __init__(self,cidr):
   self.cidr = cidr

  def get_next_cidr(self):
    lock.acquire()
    ip = IPNetwork(self.cidr)[0]
    new_ip = ipaddr.IPAddress(ip) + 256
    self.cidr = str(new_ip) + "/24"
    lock.release()
    return self.cidr
  
class Struct(object):
    def __init__(self, entries):
        self.__dict__.update(entries)

def validate_args(args):
    for key, value in args.__dict__.iteritems():
        if value == 'None':
            args.__dict__[key] = None
        if value == 'False':
            args.__dict__[key] = False
        if value == 'True':
            args.__dict__[key] = True

def update_args(ini_args, cli_args):
    for key in cli_args.keys():
        if cli_args[key]:
            ini_args[key] = cli_args[key]
    return ini_args

def create_record(vnc_lib,forwarder,rec_fqname,rec_name,rec_data,rec_type,rec_class,rec_ttl):
        
   vdns_obj = vnc_lib.virtual_DNS_read(fq_name_str = forwarder)
   vdns_rec_data = VirtualDnsRecordType(rec_name, rec_type, rec_class, rec_data, int(rec_ttl))
   vdns_rec_obj = VirtualDnsRecord(rec_name, vdns_obj, vdns_rec_data)
   vnc_lib.virtual_DNS_record_create(vdns_rec_obj)


class Tenant :
   
    def __init__(self):
       self.tenant_id = None

    def set_tenant_id(self,tenant_id):
       self.tenant_id = tenant_id

class virtual_network:

   def __init__(self,conf):
       self.vn_id = None
       self.vn_name = None

   def set_network_id(self,vn_id):
       self.vn_id = vn_id


class subnet:

   def __init__(self,conf):
      self.subnet_id = None
      self.cidr = None

   def set_subnet_id(self,subnet_id):
       self.subnet_id = subnet_id

class virtual_machines:

   def __init__(self,tenant_index,tenant_conf,connections):
       self.tenant_index = tenant_index
       self.tenant_conf = tenant_conf
       self.connections = connections

   def create_vm(self,vm_name,vn_name,vn_index,mgmt_vn_id,vn_id,image):

        global vms_info
        self.vm_name = vm_name
        self.vn_name = vn_name
        self.vn_ids = [vn_id,mgmt_vn_id]
        vm_obj = VM(self.connections)
        vm_obj.flavor="m1.small"
        vm_obj.zone="nova"

        print "VM:",vm_name,vn_id,image,mgmt_vn_id,vn_id
     
        self.vm_id  = vm_obj.create(vm_name,self.vn_ids,image)

        vm_info = {}
        vm_info['vm_name'] = self.vm_name
        vm_info['vn_name,data']  = self.vn_name
        vm_info['vn_name,mgmt']  = self.tenant_conf['virtual_networks']['mgmt_vn_name']
        vm_info['vm_id']         = self.vm_id
        vm_info['image']         = image
        vm_info['tenant_index'] = self.tenant_index
        vm_info['vn_index']     = vn_index
        vm_info['mgmt_vn_id']   = mgmt_vn_id
        vm_info['data_vn_id']   = vn_id

        vms_info[self.vm_name] = vm_info

   def get_mgmt_ip(self):
        return self.mgmt_ip
   
   def get_data_ip(self):
        return self.data_ip

   def print_params(self):
       print "TenantID:%s,NetworkID:%s,VM_Role:%s,VM_Name:%s"%(self.tenant_id,self.vn_id,self.vm_role,self.vm_name)
    
def setup_test_infra(testbed_file):
    global mylogger,inputs
    import logging
    from common.log_orig import ContrailLogger
    logging.getLogger('urllib3.connectionpool').setLevel(logging.WARN)
    logging.getLogger('paramiko.transport').setLevel(logging.WARN)
    logging.getLogger('keystoneclient.session').setLevel(logging.WARN)
    logging.getLogger('keystoneclient.httpclient').setLevel(logging.WARN)
    logging.getLogger('neutronclient.client').setLevel(logging.WARN)
    logger = ContrailLogger('SystemTest')
    logger.setUp()
    mylogger = logger.logger
    inputs = ContrailTestInit(testbed_file, logger=mylogger)
    inputs.setUp()
    connections = ContrailConnections(inputs=inputs, logger=mylogger)
    return connections

def add_virtual_dns(vnc_lib, name, domain_name, dns_domain, dyn_updates, rec_order,
                        ttl, next_vdns, fip_record, external_visible, reverse_resolution):
        domain_name_list = []
        domain_name_list.append(domain_name)
        domain_name_list_list = list(domain_name_list)
        try:
            domain_obj = vnc_lib.domain_read(fq_name=domain_name_list_list)
            print 'Domain ' + domain_name + ' found!'
        except NoIdError:
            print 'Domain ' + domain_name + ' not found!'

        if next_vdns and len(next_vdns):
          try:
           next_vdns_obj = vnc_lib.virtual_DNS_read(fq_name_str = next_vdns)
           print 'Virtual DNS ' + next_vdns + ' found!'
          except NoIdError:
           print 'Virtual DNS ' + next_vdns + ' not found!'

        vdns_str = ':'.join([domain_name, name])
        vdns_data = VirtualDnsType(domain_name=dns_domain, dynamic_records_from_client=dyn_updates, record_order=rec_order, default_ttl_seconds=int(ttl),next_virtual_DNS=next_vdns)
        #vdns_data = VirtualDnsType(dns_domain, dyn_updates, rec_order, int(ttl), next_vdns, fip_record, external_visible, reverse_resolution)

        domain_obj =  Domain(name=domain_name)
        dns_obj = VirtualDns(name, domain_obj,
                             virtual_DNS_data = vdns_data)
        vnc_lib.virtual_DNS_create(dns_obj)
        obj = vnc_lib.virtual_DNS_read(id = dns_obj.uuid)
        print "vDNS:",obj.uuid
        return obj.uuid

def parse_cli(args):

    parser = argparse.ArgumentParser(description=__doc__)
    return dict(parser.parse_known_args(args)[0]._get_kwargs())


class PerTenant :

    @retry(delay=60, tries=30)
    def wait_until_vms_deleted(self):
   
        vm_list = self.ostack_admin_obj.nova_client.servers.list(search_opts={'all_tenants': 1})
        print "VM_list:",vm_list
        vm_to_be_deleted = []
   
        for vm in vm_list:
            vm_id = vm.id
            if vm.tenant_id == re.sub("-","",self.tenant_id) :
               vm_to_be_deleted.append(vm)
   
        if len(vm_to_be_deleted) == 0 :
           return True
        else:
           return False

    def get_vms_ips(self):
        
        servers = self.tenant_nova_client.servers.list()
        print "vms_info:",vms_info
        data_vn_pattern      = self.tenant_conf['virtual_networks']['vDNS'][1]['domain_name']
        data_vn_name_pattern = re.sub('XXX',str(self.tenant_index),data_vn_pattern)
        data_vn_name_pattern = re.sub('ZZZ','2',data_vn_name_pattern)
        print "Server:",servers
        
        for server in servers:
           vm_info = vms_info[server.name]
           data_vn_name = re.sub('YYY',str(vm_info['vn_index']),data_vn_name_pattern)
           print server.name,server.name + "." + data_vn_name ,server.networks[vm_info['vn_name,data']],server.networks[vm_info['vn_name,mgmt']]

    def delete_vms(self):

        vm_list = self.ostack_admin_obj.nova_client.servers.list(search_opts={'all_tenants': 1})
        print "VM_list:",vm_list
        vm_to_be_deleted = []

        for vm in vm_list:
            vm_id = vm.id
            if vm.tenant_id == re.sub("-","",self.tenant_id) :
               vm_to_be_deleted.append(vm)

        for vm in vm_to_be_deleted:
            vm_id = vm.id
            print "deletingvm:%s"%str(vm_id)
            self.ostack_admin_obj.nova_client.servers.delete(vm_id) 

        self.wait_until_vms_deleted()

    def retrieve_existing_tenant_id(self):
        ks_client = self.ostack_admin_obj.keystone_client
        tenant_list = ks_client.tenants.list()
        for tenant in tenant_list:
           if tenant.name == self.tenant_name :
               self.tenant_id = tenant.id

    def delete_tenant(self):
        ks_client = self.ostack_admin_obj.keystone_client
        tenant_list = ks_client.tenants.list()
        for tenant in tenant_list:
           if tenant.name == self.tenant_name :
               tenant.delete()

    def delete_policys(self):

        network_policies = self.vnc_lib.network_policys_list()['network-policys']
        print "Network policies:",network_policies
        policy_name_pattern = self.tenant_conf['policies']['name']
        policy_name = re.sub('XXX',str(self.tenant_index),policy_name_pattern)
        for policy in network_policies:
           domain,project,fq_name = policy['fq_name']
           if fq_name == policy_name :
              print "Deleting policy:",fq_name
              self.vnc_lib.network_policy_delete(id=policy['uuid'])

    def cleanup(self):

        net_list = self.ostack_admin_obj.neutron_client.list_networks()['networks']
        subnet_list = self.ostack_admin_obj.neutron_client.list_subnets()['subnets']
        port_list = self.ostack_admin_obj.neutron_client.list_ports()['ports']

        self.delete_vms()
        
    
        for subnet in subnet_list :
           if subnet["tenant_id"] == re.sub("-","",self.tenant_id):
              print "Name:",subnet['name']
              subnet_id = subnet['id']
              self.ostack_admin_obj.neutron_client.delete_subnet(subnet_id)

        for net in net_list :
           if net["tenant_id"] == re.sub("-","",self.tenant_id) :
              print "Name:",net['name']
              net_id = net['id']
              self.ostack_admin_obj.neutron_client.delete_network(net_id)

        self.delete_policys()
          
        ipam_delete_list = []
        ipam_name_pattern = self.tenant_conf['virtual_networks']['IPAM']['name']
        data_ipam_name = re.sub('XXX',str(self.tenant_index),ipam_name_pattern)
        data_ipam_name = re.sub('ZZZ','2',data_ipam_name)
        data_ipam_name = re.sub('CCC','data',data_ipam_name)
        ipam_delete_list.append(data_ipam_name)

        vnc_lib = self.vnc_lib
        ipam_list = vnc_lib.network_ipams_list()['network-ipams']

        print "IPAM:",ipam_list
        for ipam in ipam_list:
           domain,project,fq_name = ipam['fq_name']
           if fq_name in ipam_delete_list:
              obj = IPAM(self.connections)
              ipam_id = ipam['uuid']
              obj.delete(ipam_id)

        #print "deleting project id :",self.tenant_id
        #self.delete_tenant()


    def get_admin_tenant_id(self):
      
        return self.admin_id

    def __init__(self,vnc_lib,connections,tenant_conf,tenant_index):

        self.vnc_lib = vnc_lib
        self.connections = connections
        self.tenant_conf  = tenant_conf
        self.tenant_index = tenant_index
        tenant_name_pattern = tenant_conf['name']
        self.tenant_name  = re.sub("XXX",str(tenant_index),tenant_name_pattern)
        self.tenant_vm_list = []
        self.ostack_admin_obj  = Openstack(OS_AUTH_URL,OS_USERNAME,OS_PASSWORD,OS_TENANT_NAME)
        self.tenant_project_obj = ConfigProject(connections)
        self.tenant_id   = None

    def create_tenant_nova_client(self):

        VERSION="2"

        auth = v2.Password(auth_url=OS_AUTH_URL,
                       username=OS_USERNAME,
                       password=OS_PASSWORD,
                       tenant_name=self.tenant_name)
        sess = session.Session(auth=auth)
        self.tenant_nova_client = nova_client.Client(VERSION, session=sess)
        #print "SERVER:",self.tenant_nova_client.servers.list()



    def clean_tenant(self):    

        tenants = self.ostack_admin_obj.keystone_client.tenants.list()
        print "tenant list:",tenants
        for tenant in tenants:
            if tenant.name == self.tenant_name :
               print "Tenant : %s available...cleaning it..."%self.tenant_name
               self.current_tenant_id = tenant.id
               self.cleanup()


class Openstack(object):

  def __init__(self,auth_url,username,password,tenant,auth_token=None):

     print "User:",auth_url,username,password,tenant,auth_token
     self.keystone_client = kclient.Client(username=username,
                                   password=password,
                                   tenant_name=tenant,
                                   auth_url=auth_url)

     if not auth_token:
       auth_token = self.keystone_client.auth_token

     self.nova_client = nova_client.Client('2',  auth_url=auth_url,
                                       username=username,
                                       api_key=password,
                                       project_id=tenant,
                                       auth_token=auth_token,
                                       insecure=True)
     ''' Get neutron client handle '''
     self.neutron_client = neutron_client.Client('2.0',
                                             auth_url=auth_url,
                                             username=username,
                                             password=password,
                                             tenant_name=tenant,
                                             insecure=True)

def generate_vdns_list(vdns,domain_name):

    d = domain_name.split(".")
    if len(d) <= 2 :
      return vdns

    if not vdns.has_key(domain_name):
       vdns[domain_name] = vDNSInfo(domain_name)
       generate_vdns_list(vdns,".".join(d[1:]))

    return vdns


def get_mysql_token():

    fptr = open("/etc/contrail/mysql.token","r")
    return fptr.readline().strip()

class vDNSInfo:

      def __init__(self,domain_name):
        self.domain_name = domain_name
        d = domain_name.split(".")

        if len(d) == 2 :
          self.forwarder = None
        else:
          self.forwarder = ".".join(d[1:])
 
      def get_domain(self):
         return self.domain_name

      def set_uuid(self,uuid):
         self.uuid = uuid

      def get_uuid(self):
         return self.uuid

      def get_forwarder(self):
         return self.forwarder

 
class Test(object):

    def __init__(self,yaml_global_conf,ini_global_conf,test_conf):

        self.yaml_global_conf = yaml_global_conf
        self.ini_global_conf = ini_global_conf
        self.test_conf = test_conf
        self.uuid = dict()
        self.tenant_ids = list()
        self.vm_connections_map = dict()
        self.ostack_admin_obj = Openstack(OS_AUTH_URL,OS_USERNAME,OS_PASSWORD,OS_TENANT_NAME)

        #self.mysql_passwd = get_mysql_token()
        self.tenants_list = list()

    def get_admin_tenant_id(self):

      return self.connections.get_auth_h().get_project_id('default_domain',OS_USERNAME)


    #def create_tenant(self,queue,tenant_conf,tenant_index):
    def create_tenant(self,tenant_conf,tenant_index):


            tenant_name_pattern = tenant_conf['name']
            tenant_name  = re.sub("XXX",str(tenant_index),tenant_name_pattern)
            #tenant_obj = PerTenant(self.vnc_lib,self.connections,tenant_conf,tenant_index)
            #tenant_id = tenant_obj.tenant_project_obj.create(tenant_name)
            tenant_obj = ConfigProject(self.connections)
            tenant_id = tenant_obj.create(tenant_name)
            tenant_obj.tenant_id = tenant_id
            self.db.set_project_id(tenant_name,tenant_id)
            tenant_connections = tenant_obj.get_connections()
            tenant_obj.update_default_sg(uuid=tenant_id)
            import pdb;pdb.set_trace()
            ipam_name_pattern   = tenant_conf['virtual_networks']['IPAM']['name']
             
            vm_index = 0
            tenant_obj.create_tenant_nova_client()
        
            vm_name_pattern          = tenant_conf['virtual_networks']['virtual_machines']['name']
            ipam_name_pattern        = tenant_conf['virtual_networks']['IPAM']['name']
            data_domain_name_pattern = tenant_conf['virtual_networks']['vDNS'][1]['domain_name']
            vn_name_pattern          = tenant_conf['virtual_networks']['data_vn_name']
            vns_count                = tenant_conf['virtual_networks']['count']
            vm_count                 = tenant_conf['virtual_networks']['virtual_machines']['count'] 

            data_ipam_name = re.sub('XXX',str(tenant_index),ipam_name_pattern)
            data_ipam_name = re.sub('ZZZ','2',data_ipam_name)
            data_ipam_name = re.sub('CCC','data',data_ipam_name)

            data_domain_name = re.sub('XXX',str(tenant_index),data_domain_name_pattern)
            data_domain_name = re.sub('ZZZ','2',data_domain_name)
             
            data_vdns_id = self.global_vdns[data_domain_name].get_uuid()

            ipam_list = self.vnc_lib.network_ipams_list()['network-ipams']
            print "Existing IPAM list:",ipam_list
            ipam_delete_list = [data_ipam_name]

            for ipam in ipam_list:
               domain,project,fq_name = ipam['fq_name']
               if fq_name in ipam_delete_list:
                  obj = IPAM(tenant_connections)
                  ipam_id = ipam['uuid']
                  obj.delete(ipam_id)
                  self.db.delete_ipam(fq_name)

            ipam_obj = IPAM(tenant_connections)
            data_ipam_id = ipam_obj.create(data_ipam_name, data_vdns_id)
            print "DATA IPAM:",data_ipam_id

            policy_name_pattern = tenant_conf['policies']['name']

            allow_rules_network = tenant_conf['policies']['rules']['allow_rules_network']
            allow_rules_port    = tenant_conf['policies']['rules']['allow_rules_port']

            rules = []

            for rule_index in xrange(len(allow_rules_network)):
                 

               r = allow_rules_network[rule_index].split()
               src_nw = r[0]
               dst_nw = r[1]
               r = allow_rules_port[rule_index].split()
               src_port = r[0]
               dst_port = r[1]
               rule = {
                           'direction': '<>', 'simple_action': 'pass',
                           'protocol': 'any',
                           'src_ports': '%s'%src_port, 'dst_ports': '%s'%dst_port,
                           'source_network': '%s'%src_nw, 'dest_network': '%s'%dst_nw,
                       } 

               print "rule:",rule
               rules.append(rule)

            tenant_connections.quantum_h = tenant_connections.get_network_h()
            tenant_connections.api_server_inspect = tenant_connections.get_api_server_inspect_handles()

            policy_name = re.sub('XXX',str(tenant_index),policy_name_pattern)
            policy_obj = Policy(tenant_connections)
            policy_obj.policy_name = policy_name
            print "policy name:",policy_name
            policy_id = policy_obj.create(tenant_connections.inputs,policy_name,rules,tenant_connections)

            for vn_index in xrange(vns_count):

               vn_name = re.sub('XXX',str(tenant_index),vn_name_pattern)
               vn_name = re.sub('YYY',str(vn_index),vn_name)
               
               data_vn_name = re.sub('CCC','data',vn_name)

               vn_obj = VN(tenant_connections)
               self.vn_name = data_vn_name
               cidr = data_cidr_obj.get_next_cidr()
               print "CIDR:",cidr
               subnets = [{'cidr':cidr,'name':self.vn_name+"_subnet"}]
               print "creating VN:",self.vn_name,subnets,data_ipam_id
               data_vn_id = vn_obj.create(self.vn_name,subnets=subnets,ipam_id=data_ipam_id)
               vn_obj.vn_id = data_vn_id
               vn_obj.add_policy(policy_obj.fixture)
            
               for c in xrange(vm_count):
                 vm_name = "vm%d"%vm_index
                 vm_index += 1
                 vm_obj = virtual_machines(tenant_index,tenant_conf,tenant_connections)
                 vm_obj.create_vm(vm_name,self.vn_name,vn_index,self.mgmt_vn_id,data_vn_id,self.glance_image)

            #queue.put(1)
            return tenant_obj

    def delete_vdns(self,root_domain,fq_name):

       self.vnc_lib=self.vnc_lib_fixture.get_handle()
       vnc_lib = self.vnc_lib
       vdns_list = vnc_lib.virtual_DNSs_list()['virtual-DNSs']
       child_list = []
       current_vdns_uuid = ""
       for vdns in vdns_list :
          vdns_obj = vnc_lib.virtual_DNS_read(vdns["fq_name"])
          vdns_data = vdns_obj.get_virtual_DNS_data()
          if ":".join(vdns["fq_name"]) == fq_name :
             current_vdns_uuid = vdns['uuid']
          if vdns_data.get_next_virtual_DNS() == fq_name :
             child_list.append(vdns)

       if len(child_list) == 0 :
           
           if fq_name == root_domain :
               print "INFO: reached root domain :%s..clean up done"%root_domain
               return
           else:
               print "deleting vdns:",fq_name
               vnc_lib.virtual_DNS_delete(id=current_vdns_uuid)
               return
       else:
           for vdns in child_list:
             self.delete_vdns(root_domain,":".join(vdns['fq_name']))
      
    def cleanup_ipam(self):

       vnc_lib = self.vnc_lib
       ipam_list = vnc_lib.network_ipams_list()['network-ipams']
       print "IPAM:",ipam_list
       
       tenant_count = self.test_conf['tenants']['count']

       ipam_name_pattern = self.test_conf['tenants']['virtual_networks']['IPAM']['name']
       mgmt_ipam_name = re.sub('ZZZ','2',ipam_name_pattern)
       mgmt_ipam_name = re.sub('CCC','mgmt',mgmt_ipam_name)


       ipam_delete_list = []
       ipam_delete_list.append(mgmt_ipam_name)

       for tenant_index in xrange(tenant_count):
           data_ipam_name = re.sub('XXX',str(tenant_index),ipam_name_pattern)
           data_ipam_name = re.sub('ZZZ','2',data_ipam_name)
           data_ipam_name = re.sub('CCC','data',data_ipam_name)
           ipam_delete_list.append(data_ipam_name)

       for ipam in ipam_list:
           domain,project,fq_name = ipam['fq_name']
           if fq_name in ipam_delete_list:
              obj = IPAM(self.connections)
              ipam_id = ipam['uuid']
              obj.delete(ipam_id)


    def cleanup_vdns(self):

       vnc_lib = self.vnc_lib
       dns_records = vnc_lib.virtual_DNS_records_list()['virtual-DNS-records']
       
       for dns_record in dns_records :
           vnc_lib.virtual_DNS_record_delete(fq_name=dns_record['fq_name'])

       root_domain = 'default-domain:soln-com'
       for i in xrange(5):
         vdns_list = vnc_lib.virtual_DNSs_list()['virtual-DNSs']
         if len(vdns_list) == 0:
           break 
         self.delete_vdns(root_domain,root_domain)


    def create_global_vdns(self):

        connections = ContrailConnections(inputs=inputs, logger=mylogger)
        auth_host   = connections.inputs.get_auth_host()
        
        vnc_lib_fixture = VncLibHelper(
                     username=connections.inputs.stack_user, password=connections.inputs.stack_password,
                     domain=connections.inputs.domain_name, project=connections.project_name,
                     inputs=connections.inputs, cfgm_ip=connections.inputs.cfgm_ip,
                     api_port=connections.inputs.api_server_port, auth_host=auth_host)
        vnc_lib = vnc_lib_fixture.get_handle()
        ipam_list = vnc_lib.network_ipams_list()['network-ipams']
        print "IPAM:",ipam_list

        vdns_list    = self.test_conf['tenants']['virtual_networks']['vDNS']
        tenant_count = self.test_conf['tenants']['count']
        vn_count     = self.test_conf['tenants']['virtual_networks']['count']

        domain_list = []

        for index,vdns in enumerate(vdns_list):
           print index,vdns
           domain_name_test = vdns['domain_name']
           domain_name_test = re.sub('ZZZ','2',domain_name_test)
           if index == 0 : # MGMT network
              domain_list.append(domain_name_test)   
              continue
           for tenant_index in xrange(tenant_count):
              domain_name_tenant = re.sub('XXX',str(tenant_index),domain_name_test)
              domain_list.append(domain_name_tenant)

        print "Domain list:",domain_list
        self.global_vdns = {}
        
        for domain in domain_list:
          self.global_vdns = generate_vdns_list(self.global_vdns,domain)


        self.vdns_info = {}
        for k,v in self.global_vdns.iteritems():
           l = len(v.get_domain().split("."))
           if self.vdns_info.has_key(l):
              self.vdns_info[l].append(v)
           else:
              self.vdns_info[l] = [v]

        vdns_dyn_updates = self.test_conf['tenants']['virtual_networks']['vDNS'][0]['dyn_updates']
        vdns_rec_order   = self.test_conf['tenants']['virtual_networks']['vDNS'][0]['rec_resolution_order'] 
        vdns_ttl         = self.test_conf['tenants']['virtual_networks']['vDNS'][0]['ttl'] 
        vdns_fip_record  = self.test_conf['tenants']['virtual_networks']['vDNS'][0]['floating_ip_record']
        vdns_external_visible   = self.test_conf['tenants']['virtual_networks']['vDNS'][0]['external_visible']
        vdns_reverse_resolution = self.test_conf['tenants']['virtual_networks']['vDNS'][0]['reverse_resolution']

        rec_type   = "NS"
        rec_class  = "IN"
        rec_ttl    = 86400

        vdns_next_vdns = None
        try:
          add_virtual_dns(vnc_lib,"soln-com","default-domain","soln.com",vdns_dyn_updates,vdns_rec_order,vdns_ttl,vdns_next_vdns,vdns_fip_record,vdns_external_visible,vdns_reverse_resolution)
        except:
          pass


        for l in sorted(self.vdns_info.keys()):

          vdns_l = self.vdns_info[l]
          for vdns in vdns_l:
           
              vdns_domain_name = vdns.get_domain()
              vdns_name = re.sub("\.","-",vdns_domain_name)
              vdns_next_vdns = "default-domain:" + re.sub("\.","-",vdns.get_forwarder())
              vdns_id = add_virtual_dns(vnc_lib,vdns_name,"default-domain",vdns_domain_name,vdns_dyn_updates,vdns_rec_order,vdns_ttl,vdns_next_vdns,vdns_fip_record,vdns_external_visible,vdns_reverse_resolution)
              vdns.set_uuid(vdns_id)
              forwarder  = vdns_next_vdns
              rec_fqname = vdns_domain_name
              rec_name   = re.sub("\.","-",vdns_domain_name)
              rec_data   = "default-domain:%s"%re.sub("\.","-",rec_fqname)

             
              create_record(vnc_lib,forwarder,rec_fqname,rec_name,rec_data,rec_type,rec_class,rec_ttl)

    def read_from_queue(self, process, kwargs):
         try:
             self.tenants_list.append(kwargs['queue'].get(
                               timeout=self.timeout))
         except Empty:
             process.terminate()


    def setUp(self):
        
        global data_cidr_obj,default_sg_id,vms_info
        vms_info = {}

        tenants      = self.test_conf['tenants']
        tenant_count = tenants['count'] 
        vns_count    = tenants['virtual_networks']['count'] 
        subnet_count = tenants['virtual_networks']['subnets'][0]['count']
        vm_count     = tenants['virtual_networks']['virtual_machines']['count'] 
          
         
        self.tenant_name_pattern = tenants['name']

        self.glance_image      = self.ini_global_conf['GLOBALS']['glance_image_name']
        cidr_start        = tenants['virtual_networks']['subnets'][0]['cidr']
        mgmt_cidr_obj     = CIDR(cidr_start)
        cidr_start        = tenants['virtual_networks']['subnets'][1]['cidr'] 
        data_cidr_obj     = CIDR(cidr_start)
        vm_obj_list = []

        self.db = TestDB("/var/tmp/db.test")

        self.connections = setup_test_infra(self.ini_global_conf['ENV']['testbed_file'])
        auth_host = self.connections.inputs.get_auth_host()
        self.vnc_lib_fixture = VncLibHelper(
                username=self.connections.inputs.stack_user, password=self.connections.inputs.stack_password,
                domain=self.connections.inputs.domain_name, project=self.connections.project_name,
                inputs=self.connections.inputs, cfgm_ip=self.connections.inputs.cfgm_ip,
                api_port=self.connections.inputs.api_server_port, auth_host=auth_host)
        self.vnc_lib = self.vnc_lib_fixture.get_handle()
        self.vnc = self.connections.get_vnc_lib_h().get_handle()

        net_list = self.ostack_admin_obj.neutron_client.list_networks()['networks']
        subnet_list = self.ostack_admin_obj.neutron_client.list_subnets()['subnets']
        port_list = self.ostack_admin_obj.neutron_client.list_ports()['ports']

        mgmt_vn = self.test_conf['tenants']['virtual_networks']['mgmt_vn_name']
        mgmt_subnet = mgmt_vn + "_subnet"
        
        tenant_name_pattern = self.test_conf['tenants']['name']

        tenant_obj_l = []
        for tenant_index in xrange(tenant_count):
           tenant_name  = re.sub("XXX",str(tenant_index),tenant_name_pattern)
           print "Tenant-name",tenant_name
           tenant_obj       = PerTenant(self.vnc_lib,self.connections,self.test_conf['tenants'],tenant_index)
           tenant_obj.retrieve_existing_tenant_id()
           tenant_obj.clean_tenant()
           tenant_obj_l.append(tenant_obj)

        for subnet in subnet_list :
           if subnet['name'] == mgmt_subnet:
              print "Name:",subnet['name']
              subnet_id = subnet['id']
              self.ostack_admin_obj.neutron_client.delete_subnet(subnet_id)

        for net in net_list :
           if net['name'] == mgmt_vn:
              print "Name:",net['name']
              net_id = net['id']
              self.ostack_admin_obj.neutron_client.delete_network(net_id)


        self.cleanup_ipam()
        self.cleanup_vdns()

        for tenant_index in xrange(tenant_count):
           tenant_name  = re.sub("XXX",str(tenant_index),tenant_name_pattern)
           print "Deleting tenant:",tenant_name
           tenant_obj_l[tenant_index].delete_tenant()

        ipam_list = self.vnc_lib.network_ipams_list()['network-ipams']
        print "IPAM:",ipam_list 
        
        import pdb;pdb.set_trace()
        self.create_global_vdns()

        mgmt_domain_name_pattern = self.test_conf['tenants']['virtual_networks']['vDNS'][0]['domain_name']
        data_domain_name_pattern = self.test_conf['tenants']['virtual_networks']['vDNS'][1]['domain_name']
        mgmt_vn_name = self.test_conf['tenants']['virtual_networks']['mgmt_vn_name']

        mgmt_domain_name = re.sub('ZZZ','2',mgmt_domain_name_pattern)

        ipam_name_pattern   = self.test_conf['tenants']['virtual_networks']['IPAM']['name']

        mgmt_ipam_name = re.sub('ZZZ','2',ipam_name_pattern)
        mgmt_ipam_name = re.sub('CCC','mgmt',mgmt_ipam_name)

        ipam_delete_list = [mgmt_ipam_name]

        ipam_list = self.vnc_lib.network_ipams_list()['network-ipams']

        for ipam in ipam_list:
           domain,project,fq_name = ipam['fq_name']
           if fq_name in ipam_delete_list:
              obj = IPAM(self.connections)
              ipam_id = ipam['uuid']
              obj.delete(ipam_id)

        mgmt_vdns_id = self.global_vdns[mgmt_domain_name].get_uuid()

        #ipam_obj     = IPAM(self.connections)
        #mgmt_ipam_id = ipam_obj.create(mgmt_ipam_name, mgmt_vdns_id)
        #print "MGMT IPAM:",mgmt_ipam_id

        #vn_obj  = VN(self.connections)
        #cidr    = mgmt_cidr_obj.get_next_cidr()
        #vn_name = mgmt_vn_name
        #subnets = [{'cidr':cidr,'name':vn_name + "_subnet"}]
        #self.mgmt_vn_id = vn_obj.create(vn_name,subnets=subnets,ipam_id=mgmt_ipam_id,disable_gateway=True,shared=True,rt_number=10000)
        self.mgmt_vn_id = None
        
        queues = list()
        kwargs_list = list()

        self.timeout = 3600
        tenant1_obj = self.create_tenant(self.test_conf['tenants'],0)
        tenant2_obj = self.create_tenant(self.test_conf['tenants'],1)
 
        return
        
        #time.sleep(10)
        #print tenant1_obj.get_vms_ips()
        #print tenant2_obj.get_vms_ips()

        tenant_count = 2

        for tenant_index in xrange(tenant_count):

            queues.append(Queue())
            kwargs_list.append({'queue': queues[tenant_index],'tenant_index': tenant_index,'tenant_conf':self.test_conf['tenants']})

        (success, timediff) = lib.create_n_process(self.create_tenant,
                                               tenant_count,
                                               kwargs_list,
                                               self.timeout,
                                               callback=self.read_from_queue)


def main():

   parser = argparse.ArgumentParser(add_help=False)
   parser.add_argument("-i", "--ini_file", default=None,help="Specify global conf file", metavar="FILE")
   parser.add_argument("-c", "--yaml_config_file", default=None,help="Specify Test conf file", metavar="FILE")

   args, remaining_argv = parser.parse_known_args(sys.argv[1:])
   cli_args = parse_cli(remaining_argv)

   ini_conf = parse_cfg_file(args.ini_file)

   yaml_conf = parse_yaml_cfg_file(args.yaml_config_file)

   print "INI_CONF:",ini_conf
   print "YAML_CONF:",yaml_conf

   #Do Global Configurations First
   #Setup MGMT VN: simple network, 1 CIDR for mgmt traffic. Set 'shared' flag for VN so that 
   #all tenants can use the same VN  
      
   yaml_global_conf = yaml_conf['global_config']

   tests = yaml_conf['tests']

   for test_conf in tests:
      test_obj = Test(yaml_global_conf,ini_conf,test_conf)
      test_obj.setUp()



main()

