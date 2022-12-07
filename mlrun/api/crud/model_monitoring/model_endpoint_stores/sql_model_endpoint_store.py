# Copyright 2018 Iguazio
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import json
import typing

import pandas as pd
import sqlalchemy
import sqlalchemy as db
from sqlalchemy.orm import sessionmaker

import mlrun
import mlrun.api.schemas
import mlrun.model_monitoring.constants as model_monitoring_constants
import mlrun.utils.model_monitoring
import mlrun.utils.v3io_clients
from mlrun.utils import logger

from .model_endpoint_store import _ModelEndpointStore


class _ModelEndpointSQLStore(_ModelEndpointStore):

    """
    Handles the DB operations when the DB target is from type SQL. For the SQL operations, we use SQLAlchemy, a Python
    SQL toolkit that handles the communication with the database.  When using SQL for storing the model endpoints
    record, the user needs to provide a valid connection string for the database.
    """

    def __init__(
        self,
        project: str,
        connection_string: str = None,
    ):
        """
        Initialize SQL store target object.

        :param project: The name of the project.
        :param connection_string: Valid connection string or a path to SQL database with model endpoints table.
        """

        super().__init__(project=project)
        self.connection_string = connection_string
        self.db = db
        self.sessionmaker = sessionmaker
        self.table_name = model_monitoring_constants.EventFieldType.MODEL_ENDPOINTS

    def write_model_endpoint(self, endpoint: mlrun.api.schemas.ModelEndpoint):
        """
        Create a new endpoint record in the SQL table. This method also creates the model endpoints table within the
        SQL database if not exist.

        :param endpoint: ModelEndpoint object that will be written into the DB.
        """

        engine = self.db.create_engine(self.connection_string)

        with engine.connect():
            if not engine.has_table(self.table_name):
                logger.info("Creating new model endpoints table in DB")
                # Define schema and table for the model endpoints table as required by the SQL table structure
                metadata = self.db.MetaData()
                self._get_table(self.table_name, metadata)

                # Create the table that stored in the MetaData object (if not exist)
                metadata.create_all(engine)

            # Retrieving the relevant attributes from the model endpoint object
            endpoint_dict = self.get_params(endpoint=endpoint)

            # Convert the result into a pandas Dataframe and write it into the database
            endpoint_df = pd.DataFrame([endpoint_dict])
            endpoint_df.to_sql(
                self.table_name, con=engine, index=False, if_exists="append"
            )

    def update_model_endpoint(self, endpoint_id: str, attributes: typing.Dict):
        """
        Update a model endpoint record with a given attributes.

        :param endpoint_id: The unique id of the model endpoint.
        :param attributes: Dictionary of attributes that will be used for update the model endpoint. Note that the keys
                           of the attributes dictionary should exist in the SQL table.

        """

        engine = self.db.create_engine(self.connection_string)
        with engine.connect():
            # Generate the sqlalchemy.schema.Table object that represents the model endpoints table
            metadata = self.db.MetaData()
            model_endpoints_table = self.db.Table(
                self.table_name, metadata, autoload=True, autoload_with=engine
            )

            # Define and execute the query with the given attributes and the related model endpoint id
            update_query = (
                self.db.update(model_endpoints_table)
                .values(attributes)
                .where(
                    model_endpoints_table.c[
                        model_monitoring_constants.EventFieldType.ENDPOINT_ID
                    ]
                    == endpoint_id
                )
            )
            engine.execute(update_query)

    def delete_model_endpoint(self, endpoint_id: str):
        """
        Deletes the SQL record of a given model endpoint id.

        :param endpoint_id: The unique id of the model endpoint.
        """
        engine = self.db.create_engine(self.connection_string)
        with engine.connect():
            # Generate the sqlalchemy.schema.Table object that represents the model endpoints table
            metadata = self.db.MetaData()
            model_endpoints_table = self.db.Table(
                self.table_name, metadata, autoload=True, autoload_with=engine
            )

            # Delete the model endpoint record using sqlalchemy ORM
            session = self.sessionmaker(bind=engine)()
            session.query(model_endpoints_table).filter_by(
                endpoint_id=endpoint_id
            ).delete()
            session.commit()
            session.close()

    def get_model_endpoint(
        self,
        metrics: typing.List[str] = None,
        start: str = "now-1h",
        end: str = "now",
        feature_analysis: bool = False,
        endpoint_id: str = None,
        convert_to_endpoint_object: bool = True,
    ) -> typing.Union[mlrun.api.schemas.ModelEndpoint, dict]:
        """
        Get a single model endpoint object. You can apply different time series metrics that will be added to the
        result.

        :param endpoint_id:                The unique id of the model endpoint.
        :param start:                      The start time of the metrics. Can be represented by a string containing an
                                           RFC 3339 time, a Unix timestamp in milliseconds, a relative time (`'now'` or
                                           `'now-[0-9]+[mhd]'`, where `m` = minutes, `h` = hours, and `'d'` = days), or
                                           0 for the earliest time.
        :param end:                        The end time of the metrics. Can be represented by a string containing an
                                           RFC 3339 time, a Unix timestamp in milliseconds, a relative time (`'now'` or
                                           `'now-[0-9]+[mhd]'`, where `m` = minutes, `h` = hours, and `'d'` = days),
                                           or 0 for the earliest time.
        :param metrics:                    A list of metrics to return for the model endpoint. There are pre-defined
                                           metrics for model endpoints such as predictions_per_second and
                                           latency_avg_5m but also custom metrics defined by the user. Please note that
                                           these metrics are stored in the time series DB and the results will be
                                           appeared under model_endpoint.spec.metrics.
        :param feature_analysis:           When True, the base feature statistics and current feature statistics will
                                           be added to the output of the resulting object.
        :param convert_to_endpoint_object: A boolean that indicates whether to convert the model endpoint dictionary
                                           into a ModelEndpoint or not. True by default.

        :return: A ModelEndpoint object or a model endpoint dictionary if convert_to_endpoint_object is False.
        """
        logger.info(
            "Getting model endpoint record from SQL",
            endpoint_id=endpoint_id,
        )

        engine = self.db.create_engine(self.connection_string)

        # Validate that the model endpoints table exists
        if not engine.has_table(self.table_name):
            raise mlrun.errors.MLRunNotFoundError(f"Table {self.table_name} not found")

        with engine.connect():

            # Generate the sqlalchemy.schema.Table object that represents the model endpoints table
            metadata = self.db.MetaData()
            model_endpoints_table = self.db.Table(
                self.table_name, metadata, autoload=True, autoload_with=engine
            )

            # Get the model endpoint record using sqlalchemy ORM
            from sqlalchemy.orm import sessionmaker

            session = sessionmaker(bind=engine)()

            columns = model_endpoints_table.columns.keys()
            values = (
                session.query(model_endpoints_table)
                .filter_by(endpoint_id=endpoint_id)
                .filter_by()
                .all()
            )
            session.close()

        if len(values) == 0:
            raise mlrun.errors.MLRunNotFoundError(f"Endpoint {endpoint_id} not found")

        # Convert the database values and the table columns into a python dictionary
        endpoint = dict(zip(columns, values[0]))

        if convert_to_endpoint_object:
            # Convert the model endpoint dictionary into a ModelEndpont object
            endpoint = self._convert_into_model_endpoint_object(
                endpoint=endpoint, feature_analysis=feature_analysis
            )

        # If time metrics were provided, retrieve the results from the time series DB
        if metrics:
            endpoint_metrics = self.get_endpoint_metrics(
                endpoint_id=endpoint_id,
                start=start,
                end=end,
                metrics=metrics,
            )
            if endpoint_metrics:
                endpoint.status.metrics = endpoint_metrics

        return endpoint

    def list_model_endpoints(
        self,
        model: str = None,
        function: str = None,
        labels: typing.Union[typing.List[str], typing.Dict] = None,
        top_level: bool = None,
        metrics: typing.List[str] = None,
        start: str = "now-1h",
        end: str = "now",
        uids: typing.List = None,
    ) -> mlrun.api.schemas.ModelEndpointList:
        """
        Returns a list of ModelEndpoint objects, supports filtering by model, function, labels or top level.
        By default, when no filters are applied, all available ModelEndpoint objects for the given project will
        be listed.

        :param model:           The name of the model to filter by.
        :param function:        The name of the function to filter by.
        :param labels:          A list of labels to filter by. Label filters work by either filtering a specific value
                                of a label (i.e. list("key==value")) or by looking for the existence of a given
                                key (i.e. "key").
        :param top_level:       If True will return only routers and endpoint that are NOT children of any router.
        :param metrics:         A list of metrics to return for each model endpoint. There are pre-defined metrics
                                for model endpoints such as predictions_per_second and latency_avg_5m but also custom
                                metrics defined by the user. Please note that these metrics are stored in the time
                                series DB and the results will be appeared under model_endpoint.spec.metrics.
        :param start:           The start time of the metrics. Can be represented by a string containing an RFC 3339
                                time, a Unix timestamp in milliseconds, a relative time (`'now'` or
                                `'now-[0-9]+[mhd]'`, where `m` = minutes, `h` = hours, and `'d'` = days), or 0 for the
                                 earliest time.
        :param end:              The end time of the metrics. Can be represented by a string containing an RFC 3339
                                 time, a Unix timestamp in milliseconds, a relative time (`'now'` or
                                 `'now-[0-9]+[mhd]'`, where `m` = minutes, `h` = hours, and `'d'` = days), or 0 for
                                 the earliest time.
        :param uids:             List of model endpoint unique ids to include in the result.

        :return: An object of ModelEndpointList which is literally a list of model endpoints along with some
                          metadata. To get a standard list of model endpoints use ModelEndpointList.endpoints.
        """

        engine = self.db.create_engine(self.connection_string)

        # Generate an empty ModelEndpointList that will be filled afterwards with ModelEndpoint objects
        endpoint_list = mlrun.api.schemas.model_endpoints.ModelEndpointList(
            endpoints=[]
        )
        with engine.connect():

            # Generate the sqlalchemy.schema.Table object that represents the model endpoints table
            metadata = self.db.MetaData()
            model_endpoints_table = self.db.Table(
                self.table_name, metadata, autoload=True, autoload_with=engine
            )

            # Get the model endpoint records using sqlalchemy ORM
            from sqlalchemy.orm import sessionmaker

            session = sessionmaker(bind=engine)()

            columns = model_endpoints_table.columns.keys()
            query = session.query(model_endpoints_table).filter_by(project=self.project)

            # Apply filters
            if model:
                query = self._filter_values(
                    query=query,
                    model_endpoints_table=model_endpoints_table,
                    key_filter=model_monitoring_constants.EventFieldType.MODEL,
                    filtered_values=[model],
                )
            if function:
                query = self._filter_values(
                    query=query,
                    model_endpoints_table=model_endpoints_table,
                    key_filter=model_monitoring_constants.EventFieldType.FUNCTION,
                    filtered_values=[function],
                )
            if uids:
                query = self._filter_values(
                    query=query,
                    model_endpoints_table=model_endpoints_table,
                    key_filter=model_monitoring_constants.EventFieldType.ENDPOINT_ID,
                    filtered_values=uids,
                    combined=False,
                )
            if top_level:
                node_ep = str(mlrun.utils.model_monitoring.EndpointType.NODE_EP.value)
                router_ep = str(mlrun.utils.model_monitoring.EndpointType.ROUTER.value)
                endpoint_types = [node_ep, router_ep]
                query = self._filter_values(
                    query=query,
                    model_endpoints_table=model_endpoints_table,
                    key_filter=model_monitoring_constants.EventFieldType.ENDPOINT_TYPE,
                    filtered_values=endpoint_types,
                    combined=False,
                )

            # Labels from type list won't be supported from 1.4.0
            # TODO: Remove in 1.4.0
            if labels and isinstance(labels, typing.List):
                logger.warn('Labels should be from type dictionary, not string', labels=labels,)

            # Convert the results from the DB into a ModelEndpoint object and append it to the ModelEndpointList
            for endpoint_values in query.all():
                endpoint_dict = dict(zip(columns, endpoint_values))
                # Filter labels
                if labels and labels != json.dumps(endpoint_dict.get(model_monitoring_constants.EventFieldType.LABELS)):
                    continue
                endpoint_obj = self._convert_into_model_endpoint_object(endpoint_dict)

                # If time metrics were provided, retrieve the results from the time series DB
                if metrics:
                    endpoint_metrics = self.get_endpoint_metrics(
                        endpoint_id=endpoint_obj.metadata.uid,
                        start=start,
                        end=end,
                        metrics=metrics,
                    )
                    if endpoint_metrics:
                        endpoint_obj.status.metrics = endpoint_metrics

                endpoint_list.endpoints.append(endpoint_obj)

        return endpoint_list

    @staticmethod
    def _filter_values(
        query: sqlalchemy.orm.query.Query,
        model_endpoints_table: sqlalchemy.Table,
        key_filter: str,
        filtered_values: typing.List,
        combined=True,
    ) -> sqlalchemy.orm.query.Query:
        """Filtering the SQL query object according to the provided filters.

        :param query:                 SQLAlchemy ORM query object. Includes the SELECT statements generated by the ORM
                                      for getting the model endpoint data from the SQL table.
        :param model_endpoints_table: SQLAlchemy table object that represents the model endpoints table.
        :param key_filter:            Key column to filter by.
        :param filtered_values:       List of values to filter the query the result.
        :param combined:              If true, then apply AND operator on the filtered values list. Otherwise, apply OR
                                      operator.

        return:                      SQLAlchemy ORM query object that represents the updated query with the provided
                                     filters.
        """

        # Generating a tuple with the relevant filters
        filter_query = ()
        for _filter in filtered_values:
            filter_query += (model_endpoints_table.c[key_filter] == _filter,)

        # Apply AND/OR operator on the SQL query object with the filters tuple
        if combined:
            return query.filter(sqlalchemy.and_(*filter_query))
        else:
            return query.filter(sqlalchemy.or_(*filter_query))

    def _get_table(self, table_name: str, metadata: sqlalchemy.MetaData):
        """Declaring a new SQL table object with the required model endpoints columns

        :param table_name: Model endpoints SQL table name.
        :param metadata:   SQLAlchemy MetaData object that used to describe the SQL DataBase. The below method uses the
                           MetaData object for declaring a table.
        """

        self.db.Table(
            table_name,
            metadata,
            self.db.Column(
                model_monitoring_constants.EventFieldType.ENDPOINT_ID,
                self.db.String(40),
                primary_key=True,
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.STATE, self.db.String(10)
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.PROJECT, self.db.String(40)
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.FUNCTION_URI,
                self.db.String(255),
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.MODEL, self.db.String(255)
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.MODEL_CLASS,
                self.db.String(255),
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.LABELS, self.db.Text
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.MODEL_URI, self.db.String(255)
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.STREAM_PATH, self.db.Text
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.ACTIVE, self.db.Boolean
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.MONITORING_MODE,
                self.db.String(10),
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.FEATURE_STATS, self.db.Text
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.CURRENT_STATS, self.db.Text
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.FEATURE_NAMES, self.db.Text
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.CHILDREN, self.db.Text
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.LABEL_NAMES, self.db.Text
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.TIMESTAMP, self.db.DateTime
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.ENDPOINT_TYPE,
                self.db.String(10),
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.CHILDREN_UIDS, self.db.Text
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.DRIFT_MEASURES, self.db.Text
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.DRIFT_STATUS,
                self.db.String(40),
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.MONITOR_CONFIGURATION,
                self.db.Text,
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.FEATURE_SET_URI,
                self.db.String(255),
            ),
            self.db.Column(
                model_monitoring_constants.EventLiveStats.LATENCY_AVG_5M, self.db.Float
            ),
            self.db.Column(
                model_monitoring_constants.EventLiveStats.LATENCY_AVG_1H, self.db.Float
            ),
            self.db.Column(
                model_monitoring_constants.EventLiveStats.PREDICTIONS_PER_SECOND,
                self.db.Float,
            ),
            self.db.Column(
                model_monitoring_constants.EventLiveStats.PREDICTIONS_COUNT_5M,
                self.db.Float,
            ),
            self.db.Column(
                model_monitoring_constants.EventLiveStats.PREDICTIONS_COUNT_1H,
                self.db.Float,
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.FIRST_REQUEST,
                self.db.String(40),
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.LAST_REQUEST,
                self.db.String(40),
            ),
            self.db.Column(
                model_monitoring_constants.EventFieldType.ERROR_COUNT, self.db.Integer
            ),
        )

    def delete_model_endpoints_resources(
        self, endpoints: mlrun.api.schemas.model_endpoints.ModelEndpointList
    ):
        """
        Delete all model endpoints resources in both SQL and the time series DB.

        :param endpoints: An object of ModelEndpointList which is literally a list of model endpoints along with some
                          metadata. To get a standard list of model endpoints use ModelEndpointList.endpoints.
        """

        # Delete model endpoint record from SQL table
        for endpoint in endpoints.endpoints:
            self.delete_model_endpoint(
                endpoint.metadata.uid,
            )
