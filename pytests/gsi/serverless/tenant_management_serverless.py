"""tenant_management.py: "This class test cluster affinity  for GSI"

__author__ = "Pavan PB"
__maintainer = Pavan PB"
__email__ = "pavan.pb@couchbase.com"
__git_user__ = "pavan-couchbase"
"""
import random
import time

import requests

from gsi.serverless.base_gsi_serverless import BaseGSIServerless
from membase.api.serverless_rest_client import ServerlessRestConnection as RestConnection
from testconstants import INDEX_MAX_CAP_PER_TENANT
from couchbase_helper.query_definitions import QueryDefinition
from lib.capella.utils import ServerlessDataPlane
from concurrent.futures import ThreadPoolExecutor
from threading import Event


class TenantManagement(BaseGSIServerless):
    def setUp(self):
        super(TenantManagement, self).setUp()
        self.log.info("==============  TenantManagement serverless setup has started ==============")

    def tearDown(self):
        self.log.info("==============  TenantManagement serverless tearDown has started ==============")
        super(TenantManagement, self).tearDown()
        self.log.info("==============  TenantManagement serverless tearDown has completed ==============")

    def suite_tearDown(self):
        pass

    def suite_setUp(self):
        pass

    def test_cluster_affinity(self):
        self.provision_databases(count=self.num_of_tenants)
        self.create_scopes_collections(databases=self.databases.values())
        for counter, database in enumerate(self.databases.values()):
            for scope in database.collections:
                for collection in database.collections[scope]:
                    for index in range(self.num_of_indexes_per_tenant):
                        index1 = f'idx_a_{index}_db{counter}_{random.randint(0, 1000)}'
                        index2 = f'idx_b_{index}_db{counter}_{random.randint(0, 1000)}'
                        index1_replica, index2_replica = f'{index1} (replica 1)', f'{index2} (replica 1)'
                        self.create_index(database, query_statement=f"create index {index1} on `{database.id}`.{scope}.{collection}(a)",
                                          use_sdk=self.use_sdk)
                        self.create_index(database, query_statement=f"create index {index2} on `{database.id}`.{scope}.{collection}(b)",
                                          use_sdk=self.use_sdk)
                        self.rest_obj = RestConnection(database.admin_username, database.admin_password, database.rest_host)
                        rest_info = self.create_rest_info_obj(username=database.admin_username,
                                                              password=database.admin_password,
                                                              rest_host=database.rest_host)
                        nodes_obj = self.rest_obj.get_all_dataplane_nodes()
                        self.log.debug(f"Dataplane nodes object {nodes_obj}")
                        for node in nodes_obj:
                            if 'index' in node['services']:
                                host1 = self.get_resident_host_for_index(index_name=index1, rest_info=rest_info,
                                                                         indexer_node=node['hostname'].split(":")[0])
                                host1_replica = self.get_resident_host_for_index(index_name=index1_replica,
                                                                                 rest_info=rest_info,
                                                                                 indexer_node=node['hostname'].split(":")[0])
                                host2 = self.get_resident_host_for_index(index_name=index2, rest_info=rest_info,
                                                                         indexer_node=node['hostname'].split(":")[0])
                                host2_replica = self.get_resident_host_for_index(index_name=index2_replica,
                                                                                 rest_info=rest_info,
                                                                                 indexer_node=node['hostname'].split(":")[0])
                                break
                        self.log.info(
                            f"Hosts on which indexes reside. index1 {host1} and index1 replica {host1_replica}. "
                            f"index2 {host2}and index2 replica {host2_replica}")
                        if all(item is not None for item in [host1, host2, host1_replica, host2_replica]):
                            if host1 != host2 and host1 != host2_replica:
                                self.fail(
                                    f"Index tenant affinity not honoured. Index1 is on host {host1}. Index2 is on host {host2}. "
                                    f"Index1 replica is on host {host2}. Index2 replica is on host {host2_replica}")
                            if host2 != host1 and host2 != host1_replica:
                                self.fail(
                                    f"Index tenant affinity not honoured. Index1 is on host {host1}. Index2 is on host {host2}. "
                                    f"Index1 replica is on host {host2}. Index2 replica is on host {host2_replica}")
                        else:
                            self.fail("Not all indexes (or their replicas) have been created. Test failure")

    def test_max_limit_indexes_per_tenant(self):
        self.provision_databases(count=self.num_of_tenants)
        for counter, database in enumerate(self.databases.values()):
            self.cleanup_database(database_obj=database)
            for index_num in range(INDEX_MAX_CAP_PER_TENANT):
                try:
                    self.create_index(database,
                                      query_statement=f"create index idx_db_{counter}_{index_num + 1} on _default(b)",
                                      use_sdk=self.use_sdk)
                except Exception as e:
                    self.fail(
                        f"Index creation fails despite not reaching the limit of {INDEX_MAX_CAP_PER_TENANT}."
                        f" No of indexes created: {index_num}")
            try:
                num_of_indexes = self.get_count_of_indexes_for_tenant(database_obj=database)
                self.create_index(database,
                                  query_statement=f"create index idx{INDEX_MAX_CAP_PER_TENANT} on _default(b)",
                                  use_sdk=self.use_sdk)
                self.fail(
                    f"Index creation still works despite reaching limit of {INDEX_MAX_CAP_PER_TENANT}."
                    f" No of indexes already created{num_of_indexes}")
            except Exception as e:
                self.log.info(
                    f"Error seen {str(e)} while trying to create index number {INDEX_MAX_CAP_PER_TENANT} as expected")

    def test_query_node_not_co_located(self):
        self.provision_databases(count=self.num_of_tenants)
        for counter, database in enumerate(self.databases.values()):
            self.rest_obj = RestConnection(database.admin_username, database.admin_password, database.rest_host)
            nodes_obj = self.rest_obj.get_all_dataplane_nodes()
            for node in nodes_obj:
                if 'n1ql' in node['services'] and len(node['services']) > 1:
                    self.fail(f"Node {node['hostname']} has multiple services {node['services']}")

    def test_run_queries_against_all_query_nodes(self):
        self.provision_databases(count=self.num_of_tenants)
        for counter, database in enumerate(self.databases.values()):
            index_name = f'#primary'
            namespace = f"default:`{database.id}`._default._default"
            self.rest_obj = RestConnection(database.admin_username, database.admin_password, database.rest_host)
            nodes_obj = self.rest_obj.get_all_dataplane_nodes()
            self.load_databases(load_all_databases=False, num_of_docs=10,
                                database_obj=database, scope="_default", collection="_default")
            query_gen = QueryDefinition(index_name=index_name)
            query = query_gen.generate_primary_index_create_query(defer_build=self.defer_build, namespace=namespace)
            self.run_query(database=database, query=query)
            for node in nodes_obj:
                if 'n1ql' in node['services']:
                    resp_json = self.run_query(database=database, query="select count(*) from _default",
                                               query_node=node['hostname'].split(":")[0])
                    if resp_json['status'] != 'success' or resp_json['results'][0]['$1'] != 10:
                        self.fail(f"Count query errored out. Response is {resp_json}")
                    self.log.info(f"Response is {resp_json}")

    def test_run_queries_against_different_tenant(self):
        self.provision_databases(count=self.num_of_tenants)
        index_fields = ['age', 'country']
        user1, password1, user2, password2 = None, None, None, None
        queries = ['select * from _default where age>20', 'select * from _default where country like "A%"']
        for counter, database in enumerate(self.databases.values()):
            index_name = f'idx_db{counter}'
            self.create_index(database,
                              query_statement=f"create index {index_name} on _default({index_fields[counter]})")
            self.rest_obj = RestConnection(database.admin_username, database.admin_password, database.rest_host)
            self.load_databases(load_all_databases=False, num_of_docs=10,
                                database_obj=database, scope="_default", collection="_default")
            if counter == 0:
                user1, password1 = database.access_key, database.secret_key
            else:
                user2, password2 = database.access_key, database.secret_key
        for counter, database in enumerate(self.databases.values()):
            test_pass = False
            if counter == 0:
                user, password = user2, password2
            else:
                user, password = user1, password1
            try:
                self.run_query(database=database, username=user, password=password, query=queries[counter])
                print("Query ran without errors. Test failure")
                test_pass = False
            except requests.exceptions.HTTPError as err:
                print(str(err))
                if '401 Client Error: Unauthorized' in str(err):
                    test_pass = True
            if not test_pass:
                self.fail(
                    f"User {user} with password {password} able to run queries on {database.id} a database the user is unauthorised for")

    # def test_scale_up_number_of_tenants(self):
    # WIP. Need to stabilise this
    #     if not self.dataplane_id:
    #         self.fail("This test needs a dataplane_id parameter in the conf file to run")
    #     dataplane = ServerlessDataPlane(self.dataplane_id)
    #     rest_api_info = self.api.get_access_to_serverless_dataplane_nodes(dataplane_id=self.dataplane_id)
    #     dataplane.populate(rest_api_info)
    #     index_nodes_before = self.get_nodes_from_services_map(dataplane=dataplane, service="index")
    #     self.log.info(f"Index nodes before the test run:{index_nodes_before}")
    #     for counter, database in enumerate(self.databases.values()):
    #         index_nodes = self.get_nodes_from_services_map(database, "index")
    #         index_nodes_before.add(index_nodes)
    #     self.provision_databases(count=self.num_of_tenants-1, seed="AlphaTest")
    #     for counter, database in enumerate(self.databases.values()):
    #         for index in range(self.num_of_indexes_per_tenant):
    #             self.load_databases(database_obj=database, num_of_docs=10)
    #             self.log.info(f"Iteration number {counter}")
    #             index1 = f'idx{index}_db{counter}_{random.randint(0, 1000)}'
    #             index1_replica = f'{index1} (replica 1)'
    #             self.create_index(database, query_statement=f"create index {index1} on _default(b{counter})",
    #                               use_sdk=self.use_sdk)
    #             self.log.info(f"Iteration number {counter}. Index creation successful")
    #             self.rest_obj = RestConnection(database.admin_username, database.admin_password, database.rest_host)
    #             nodes_obj = self.rest_obj.get_all_dataplane_nodes()
    #             self.log.debug(f"Dataplane nodes object {nodes_obj}")
    #             for node in nodes_obj:
    #                 if 'index' in node['services']:
    #                     host1 = self.get_resident_host_for_index(index_name=index1, database_obj=database,
    #                                                              indexer_node=node['hostname'].split(":")[0])
    #                     self.log.info(f"Iteration number {counter}. Host for index {index1}: is Host:{host1}")
    #                     host1_replica = self.get_resident_host_for_index(index_name=index1_replica,
    #                                                                      database_obj=database,
    #                                                                      indexer_node=node['hostname'].split(":")[0])
    #                     self.log.info(f"Iteration number {counter}. Host for index replica {index1_replica}: is Host:{host1_replica}")
    #                     break
    #             index_exists = self.check_if_index_exists(database_obj=database, index_name=index1)
    #             self.log.info(f"Iteration number {counter}. index {index1} exists? {index_exists}")
    #     index_nodes_after = self.get_nodes_from_services_map(dataplane=dataplane, service="index")
    #     if index_nodes_before == index_nodes_after:
    #         self.fail(f"Index sub-cluster did not scale despite creating indexes for {self.num_of_tenants} tenants")
    #     self.log.info(f"Index nodes before the test run:{index_nodes_after}")

    def test_scale_indexer_sub_cluster(self):
        if not self.dataplane_id:
            self.fail("This test needs a dataplane_id parameter in the conf file to run")
        dataplane = ServerlessDataPlane(self.dataplane_id)
        rest_api_info = self.api.get_access_to_serverless_dataplane_nodes(dataplane_id=self.dataplane_id)
        dataplane.populate(rest_api_info)
        rest_info = self.create_rest_info_obj(username=dataplane.admin_username,
                                              password=dataplane.admin_password,
                                              rest_host=dataplane.rest_host)
        index_nodes_before = self.get_nodes_from_services_map(rest_info=rest_info, service="index")
        self.log.info(f"Index nodes before the test run:{index_nodes_before}")
        self.provision_databases(count=self.num_of_tenants, seed="test-scale-up")
        self.create_scopes_collections(databases=self.databases.values())
        hosts = {}
        for counter, database in enumerate(self.databases.values()):
            for scope in database.collections:
                for collection in database.collections[scope]:
                    create_index_list = self.gsi_util_obj.get_create_index_list(self.definition_list,
                                                                                f"`{database.id}`.{scope}.{collection}")
                    for index_create_query in create_index_list:
                        self.run_query(database=database, query=index_create_query)
                    index_list = self.get_all_index_names(database=database)
                    self.rest_obj = RestConnection(database.admin_username, database.admin_password, database.rest_host)
                    rest_info = self.create_rest_info_obj(username=database.admin_username,
                                                          password=database.admin_password,
                                                          rest_host=database.rest_host)
                    nodes_obj = self.rest_obj.get_all_dataplane_nodes()
                    hosts[database.id] = set()
                    for node in nodes_obj:
                        for index_name in index_list:
                            if 'index' in node['services']:
                                host1 = self.get_resident_host_for_index(index_name=index_name, rest_info=rest_info,
                                                                 indexer_node=node['hostname'].split(":")[0])
                                host1_replica = self.get_resident_host_for_index(index_name=f'{index_name} (replica 1)', rest_info=rest_info,
                                                                 indexer_node=node['hostname'].split(":")[0])
                                if not host1:
                                    self.fail(f"Index {index_name} not found on any of the hosts")
                                if not host1_replica:
                                    self.fail(f"Index {index_name} not found on any of the hosts")
                                if not hosts[database.id]:
                                    if host1:
                                        hosts[database.id].add(host1[0])
                                    if host1_replica:
                                        hosts[database.id].add(host1_replica[0])
                                elif host1[0] not in hosts[database.id] and host1_replica[0] not in hosts[database.id]:
                                    self.fail(f"Index tenant affinity not honored for index name {index_name}. Host it resides on"
                                              f"{host1}. Replica host {host1_replica}. Indexes have so far only been created on these hosts {hosts}")
        self.load_data_new_doc_loader(databases=self.databases.values(), doc_start=0, doc_end=self.total_doc_count)
        with ThreadPoolExecutor() as executor:
            event = Event()
            future = executor.submit(self.run_parallel_workloads, event)
            self.scale_up_index_subcluster(dataplane=dataplane)
            event.set()
            future.result()
        index_nodes_after = self.get_nodes_from_services_map(rest_info=rest_info, service="index")
        if index_nodes_before == index_nodes_after:
            self.fail(f"Index sub-cluster did not scale despite creating indexes for {self.num_of_tenants} tenants")
        self.log.info(f"Index nodes after the test run:{index_nodes_after}")
        for counter, database in enumerate(self.databases.values()):
            for scope in database.collections:
                for collection in database.collections[scope]:
                    index1 = f'idx__db0_{random.randint(0, 1000)}'
                    index1_replica = f'{index1} (replica 1)'
                    self.create_index(database, query_statement=f"create index {index1}_db{counter} on f`{database.id}`.{scope}.{collection}(a)",
                                          use_sdk=self.use_sdk)
                    self.rest_obj = RestConnection(dataplane.admin_username, dataplane.admin_password, dataplane.rest_host)
                    nodes_obj = self.rest_obj.get_all_dataplane_nodes()
                    for node in nodes_obj:
                        if 'index' in node['services']:
                            host1_after_scale = self.get_resident_host_for_index(index_name=index1, rest_info=rest_info,
                                                                     indexer_node=node['hostname'].split(":")[0])
                            host1_replica_after_scale = self.get_resident_host_for_index(index_name=index1_replica,
                                                                             rest_info=rest_info,
                                                                             indexer_node=node['hostname'].split(":")[0])
                            break
                    self.log.info(
                        f"Hosts on which indexes reside. index1 {host1_after_scale} and index1 replica {host1_replica_after_scale}")
                    if all(item is not None for item in [host1_after_scale, host1_replica_after_scale]):
                        if host1_after_scale[0] not in hosts[database.id] and host1_replica_after_scale[0] not in hosts[database.id]:
                            self.fail(f"Tenant affinity not honored after scaling. After scaling, the index {index1} resides on"
                                      f"host {host1_after_scale}. Replica resides on {host1_replica_after_scale}, but the "
                                      f"indexes created before reside on {hosts[database.id]}")

        with ThreadPoolExecutor() as executor:
            event = Event()
            future = executor.submit(self.run_parallel_workloads, event)
            self.scale_down_index_subcluster(dataplane=dataplane)
            event.set()
            future.result()
        # run queries and mutations after scale-up
        with ThreadPoolExecutor() as executor:
            event = Event()
            future = executor.submit(self.run_parallel_workloads, event)
            time.sleep(120)
            event.set()
            future.result()
        index_nodes_after_2 = self.get_nodes_from_services_map(rest_info=rest_info, service="index")
        if index_nodes_after == index_nodes_after_2:
            self.fail(f"Index sub-cluster did not scale down despite creating indexes for {self.num_of_tenants} tenants")
        self.log.info(f"Index nodes after the test run:{index_nodes_after}")
        index1 = f'idx__db0_{random.randint(0, 1000)}'
        index1_replica = f'{index1} (replica 1)'
        for counter, database in enumerate(self.databases.values()):
            for scope in database.collections:
                for collection in database.collections[scope]:
                    self.create_index(database, query_statement=f"create index {index1}_db{counter} on `{database.id}`.{scope}.{collection}(a)",
                                      use_sdk=self.use_sdk)
                    self.rest_obj = RestConnection(dataplane.admin_username, dataplane.admin_password, dataplane.rest_host)
                    nodes_obj = self.rest_obj.get_all_dataplane_nodes()
                    for node in nodes_obj:
                        if 'index' in node['services']:
                            host1_after_scale_down = self.get_resident_host_for_index(index_name=index1, rest_info=rest_info,
                                                                                 indexer_node=node['hostname'].split(":")[0])
                            host1_replica_after_scale_down = self.get_resident_host_for_index(index_name=index1_replica,
                                                                                         rest_info=rest_info,
                                                                                         indexer_node=
                                                                                         node['hostname'].split(":")[0])
                            break
                    self.log.info(
                        f"Hosts on which indexes reside. index1 {host1_after_scale_down} and index1 replica {host1_replica_after_scale_down}")
                    if all(item is not None for item in [host1_after_scale_down, host1_replica_after_scale_down]):
                        if host1_after_scale[0] not in hosts[database.id] and host1_replica_after_scale[0] not in hosts[
                            database.id]:
                            self.fail(f"Tenant affinity not honored after scaling. After scaling, the index {index1} resides on"
                                      f"host {host1_after_scale}. Replica resides on {host1_replica_after_scale}, but the "
                                      f"indexes created before reside on {hosts[database.id]}")
        # run queries and mutations after scale down
        with ThreadPoolExecutor() as executor:
            event = Event()
            future = executor.submit(self.run_parallel_workloads, event)
            time.sleep(120)
            event.set()
            future.result()
