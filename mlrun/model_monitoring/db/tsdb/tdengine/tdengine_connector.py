# Copyright 2024 Iguazio
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

import typing
from datetime import datetime
from io import StringIO
import pandas as pd
import taosws
from mlrun.common.schemas.model_monitoring.model_endpoints import _compose_full_name
import mlrun.common.schemas.model_monitoring as mm_schemas
import mlrun.model_monitoring.db.tsdb.tdengine.schemas as tdengine_schemas
import mlrun.model_monitoring.db.tsdb.tdengine.stream_graph_steps
import mlrun.model_monitoring.helpers
from mlrun.model_monitoring.db import TSDBConnector
from mlrun.utils import logger


class TDEngineConnector(TSDBConnector):
    """
    Handles the TSDB operations when the TSDB connector is of type TDEngine.
    """

    type: str = mm_schemas.TSDBTarget.TDEngine

    def __init__(
        self,
        project: str,
        database: str = tdengine_schemas._MODEL_MONITORING_DATABASE,
        **kwargs,
    ):
        super().__init__(project=project)
        if "connection_string" not in kwargs:
            raise mlrun.errors.MLRunInvalidArgumentError(
                "connection_string is a required parameter for TDEngineConnector."
            )
        self._tdengine_connection_string = kwargs.get("connection_string")
        self.database = database
        self._connection = self._create_connection()
        self._init_super_tables()

    def _create_connection(self):
        """Establish a connection to the TSDB server."""
        conn = taosws.connect(self._tdengine_connection_string)
        try:
            conn.execute(f"CREATE DATABASE {self.database}")
        except taosws.QueryError:
            # Database already exists
            pass
        conn.execute(f"USE {self.database}")
        return conn

    def _init_super_tables(self):
        """Initialize the super tables for the TSDB."""
        self.tables = {
            mm_schemas.TDEngineSuperTables.APP_RESULTS: tdengine_schemas.AppResultTable(),
            mm_schemas.TDEngineSuperTables.METRICS: tdengine_schemas.Metrics(),
            mm_schemas.TDEngineSuperTables.PREDICTIONS: tdengine_schemas.Predictions(),
        }

    def create_tables(self):
        """Create TDEngine supertables."""
        for table in self.tables:
            create_table_query = self.tables[table]._create_super_table_query()
            self._connection.execute(create_table_query)

    def write_application_event(
        self,
        event: dict,
        kind: mm_schemas.WriterEventKind = mm_schemas.WriterEventKind.RESULT,
    ):
        """
        Write a single result or metric to TSDB.
        """

        table_name = (
            f"{self.project}_"
            f"{event[mm_schemas.WriterEvent.ENDPOINT_ID]}_"
            f"{event[mm_schemas.WriterEvent.APPLICATION_NAME]}_"
        )
        event[mm_schemas.EventFieldType.PROJECT] = self.project

        if kind == mm_schemas.WriterEventKind.RESULT:
            # Write a new result
            table = self.tables[mm_schemas.TDEngineSuperTables.APP_RESULTS]
            table_name = (
                f"{table_name}_" f"{event[mm_schemas.ResultData.RESULT_NAME]}"
            ).replace("-", "_")

        else:
            # Write a new metric
            table = self.tables[mm_schemas.TDEngineSuperTables.METRICS]
            table_name = (
                f"{table_name}_" f"{event[mm_schemas.MetricData.METRIC_NAME]}"
            ).replace("-", "_")

        create_table_query = table._create_subtable_query(
            subtable=table_name, values=event
        )
        self._connection.execute(create_table_query)
        insert_table_query = table._insert_subtable_query(
            subtable=table_name, values=event
        )
        self._connection.execute(insert_table_query)

    def apply_monitoring_stream_steps(self, graph):
        """
        Apply TSDB steps on the provided monitoring graph. Throughout these steps, the graph stores live data of
        different key metric dictionaries. This data is being used by the monitoring dashboards in
        grafana. At the moment, we store two types of data:
        - prediction latency.
        - custom metrics.
        """

        def apply_process_before_tsdb():
            graph.add_step(
                "mlrun.model_monitoring.db.tsdb.tdengine.stream_graph_steps.ProcessBeforeTDEngine",
                name="ProcessBeforeTDEngine",
                after="MapFeatureNames",
            )

        def apply_tdengine_target(name, after):
            graph.add_step(
                "storey.TDEngineTarget",
                name=name,
                after=after,
                url=self._tdengine_connection_string,
                supertable=mm_schemas.TDEngineSuperTables.PREDICTIONS,
                table_col=mm_schemas.EventFieldType.TABLE_COLUMN,
                time_col=mm_schemas.EventFieldType.TIME,
                database=self.database,
                columns=[
                    mm_schemas.EventFieldType.LATENCY,
                    mm_schemas.EventKeyMetrics.CUSTOM_METRICS,
                ],
                tag_cols=[
                    mm_schemas.EventFieldType.PROJECT,
                    mm_schemas.EventFieldType.ENDPOINT_ID,
                ],
                max_events=10,
            )

        apply_process_before_tsdb()
        apply_tdengine_target(
            name="TDEngineTarget",
            after="ProcessBeforeTDEngine",
        )

    def delete_tsdb_resources(self):
        """
        Delete all project resources in the TSDB connector, such as model endpoints data and drift results.
        """
        for table in self.tables:
            get_subtable_names_query = self.tables[table]._get_subtables_query(
                values={mm_schemas.EventFieldType.PROJECT: self.project}
            )
            subtables = self._connection.query(get_subtable_names_query)
            for subtable in subtables:
                drop_query = self.tables[table]._drop_subtable_query(
                    subtable=subtable[0]
                )
                self._connection.execute(drop_query)
        logger.info(
            f"Deleted all project resources in the TSDB connector for project {self.project}"
        )

    def get_model_endpoint_real_time_metrics(
        self,
        endpoint_id: str,
        metrics: list[str],
        start: str,
        end: str,
    ) -> dict[str, list[tuple[str, float]]]:
        # Not implemented, use get_records() instead
        pass

    def get_records(
        self,
        table: str,
        start: str,
        end: str,
        columns: typing.Optional[list[str]] = None,
        filter_query: str = "",
        interval: str = "",
        limit: int = 0,
        agg: typing.Optional[list] = None,
        sliding_window: str = "",
        timestamp_column: str = mm_schemas.EventFieldType.TIME,
    ) -> pd.DataFrame:
        """
        Getting records from TSDB data collection.
        :param table:            Either a supertable or a subtable name.
        :param columns:          Columns to include in the result.
        :param filter_query:     Optional filter expression as a string. The filter structure depends on the TSDB
                                 connector type.
        :param start:            The start time of the metrics.
        :param end:              The end time of the metrics.
        :param timestamp_column: The column name that holds the timestamp.

        :return: DataFrame with the provided attributes from the data collection.
        :raise:  MLRunInvalidArgumentError if query the provided table failed.
        """
        with StringIO() as query:
            if filter_query:
                query.write(filter_query)
                query.write(' and ')
            query.write(f"project = '{self.project}'")
            filter_query = query.getvalue()
        print('[EYAL]: updated filter_query: ', filter_query)
        full_query = tdengine_schemas.TDEngineSchema._get_records_query(
            table=table,
            start=start,
            end=end,
            columns_to_filter=columns,
            filter_query=filter_query,
            interval = interval,
            limit = limit,
            agg = agg,
            sliding_window = sliding_window,
            timestamp_column=timestamp_column,
            database=self.database,
        )
        try:
            query_result = self._connection.query(full_query)
        except taosws.QueryError as e:
            raise mlrun.errors.MLRunInvalidArgumentError(
                f"Failed to query table {table} in database {self.database}, {str(e)}"
            )
        columns = []
        for column in query_result.fields:
            columns.append(column.name())

        return pd.DataFrame(query_result, columns=columns)

    # @staticmethod
    # def df_to_metrics_values(
    #         *,
    #         df: pd.DataFrame,
    #         metrics: list[mm_schemas.ModelEndpointMonitoringMetric],
    #         project: str,
    # ) -> list[
    #     typing.Union[
    #         mm_schemas.ModelEndpointMonitoringMetricValues,
    #         mm_schemas.ModelEndpointMonitoringMetricNoData,
    #     ]
    # ]:
    #     """
    #     Parse a time-indexed data-frame of metrics from the TSDB into a list of
    #     metrics values per distinct results.
    #     When a metric is not found in the data-frame, it is represented in no-data object.
    #     """
    #     metrics_without_data = {metric.full_name: metric for metric in metrics}
    #
    #     metrics_values: list[
    #         typing.Union[
    #             mm_schemas.ModelEndpointMonitoringMetricValues,
    #             mm_schemas.ModelEndpointMonitoringMetricNoData,
    #         ]
    #     ] = []
    #     if not df.empty:
    #         grouped = df.groupby(
    #             [
    #                 mm_schemas.WriterEvent.APPLICATION_NAME,
    #                 mm_schemas.MetricData.METRIC_NAME,
    #             ],
    #             observed=False,
    #         )
    #     else:
    #         logger.debug("No metrics", missing_metrics=metrics_without_data.keys())
    #         grouped = []
    #     for (app_name, name), sub_df in grouped:
    #         full_name = _compose_full_name(
    #             project=project,
    #             app=app_name,
    #             name=name,
    #             type=mm_schemas.ModelEndpointMonitoringMetricType.METRIC,
    #         )
    #         metrics_values.append(
    #             mm_schemas.ModelEndpointMonitoringMetricValues(
    #                 full_name=full_name,
    #                 values=list(
    #                     zip(
    #                         sub_df.index,
    #                         sub_df[mm_schemas.MetricData.METRIC_VALUE],
    #                     )
    #                 ),  # pyright: ignore[reportArgumentType]
    #             )
    #         )
    #         del metrics_without_data[full_name]
    #
    #     for metric in metrics_without_data.values():
    #         metrics_values.append(
    #             mm_schemas.ModelEndpointMonitoringMetricNoData(
    #                 full_name=metric.full_name,
    #                 type=mm_schemas.ModelEndpointMonitoringMetricType.METRIC,
    #             )
    #         )
    #
    #     return metrics_values

    # @staticmethod
    # def df_to_results_values(
    #     *,
    #     df: pd.DataFrame,
    #     metrics: list[mm_schemas.ModelEndpointMonitoringMetric],
    #     project: str,
    # ) -> list[
    #     typing.Union[
    #         mm_schemas.ModelEndpointMonitoringResultValues,
    #         mm_schemas.ModelEndpointMonitoringMetricNoData,
    #     ]
    # ]:
    #     """
    #     Parse a time-indexed data-frame of results from the TSDB into a list of
    #     results values per distinct results.
    #     When a result is not found in the data-frame, it is represented in no-data object.
    #     """
    #     metrics_without_data = {metric.full_name: metric for metric in metrics}
    #
    #     metrics_values: list[
    #         typing.Union[
    #             mm_schemas.ModelEndpointMonitoringResultValues,
    #             mm_schemas.ModelEndpointMonitoringMetricNoData,
    #         ]
    #     ] = []
    #     if not df.empty:
    #         grouped = df.groupby(
    #             [
    #                 mm_schemas.WriterEvent.APPLICATION_NAME,
    #                 mm_schemas.ResultData.RESULT_NAME,
    #             ],
    #             observed=False,
    #         )
    #     else:
    #         grouped = []
    #         logger.debug("No results", missing_results=metrics_without_data.keys())
    #     for (app_name, name), sub_df in grouped:
    #         result_kind = mlrun.model_monitoring.helpers.get_invocations_fqn(sub_df)
    #         full_name = _compose_full_name(project=project, app=app_name, name=name)
    #         metrics_values.append(
    #             mm_schemas.ModelEndpointMonitoringResultValues(
    #                 full_name=full_name,
    #                 result_kind=result_kind,
    #                 values=list(
    #                     zip(
    #                         sub_df.index,
    #                         sub_df[mm_schemas.ResultData.RESULT_VALUE],
    #                         sub_df[mm_schemas.ResultData.RESULT_STATUS],
    #                     )
    #                 ),  # pyright: ignore[reportArgumentType]
    #             )
    #         )
    #         del metrics_without_data[full_name]
    #
    #     for metric in metrics_without_data.values():
    #         if metric.full_name == mlrun.model_monitoring.helpers.get_invocations_fqn(project):
    #             continue
    #         metrics_values.append(
    #             mm_schemas.ModelEndpointMonitoringMetricNoData(
    #                 full_name=metric.full_name,
    #                 type=mm_schemas.ModelEndpointMonitoringMetricType.RESULT,
    #             )
    #         )
    #     print('[EYAL]: now return metrics mvalues: ', metrics_values)
    #     return metrics_values

    def read_metrics_data(
        self,
        *,
        endpoint_id: str,
        start: str,
        end: str,
        metrics: list[mm_schemas.ModelEndpointMonitoringMetric],
        type: typing.Literal["metrics", "results"],
    ) -> typing.Union[
        list[
            typing.Union[
                mm_schemas.ModelEndpointMonitoringResultValues,
                mm_schemas.ModelEndpointMonitoringMetricNoData,
            ],
        ],
        list[
            typing.Union[
                mm_schemas.ModelEndpointMonitoringMetricValues,
                mm_schemas.ModelEndpointMonitoringMetricNoData,
            ],
        ],
    ]:
        if type == "metrics":
            table = mm_schemas.TDEngineSuperTables.METRICS
            name = mm_schemas.MetricData.METRIC_NAME
            df_handler = self.df_to_metrics_values
        elif type == "results":
            table = mm_schemas.TDEngineSuperTables.APP_RESULTS
            name = mm_schemas.ResultData.RESULT_NAME
            df_handler = self.df_to_results_values
        else:
            raise mlrun.errors.MLRunInvalidArgumentError(
                f"Invalid type {type}, must be either 'metrics' or 'results'."
            )

        list_of_metrics = [f"({mm_schemas.WriterEvent.APPLICATION_NAME} = '{metric.app}' AND {name} = '{metric.name}')" for metric in metrics]
        with StringIO() as query:
            query.write(f"endpoint_id='{endpoint_id}' ")
            query.write("AND ")
            query.write(" OR ".join(list_of_metrics))
            filter_query = query.getvalue()


        df = self.get_records(
            table=table,
            start=start,
            end=end,
            # columns=[metric],
            filter_query=filter_query,
            timestamp_column=mm_schemas.WriterEvent.END_INFER_TIME
        )

        df[mm_schemas.WriterEvent.END_INFER_TIME] = pd.to_datetime(df[mm_schemas.WriterEvent.END_INFER_TIME])
        df.set_index(mm_schemas.WriterEvent.END_INFER_TIME, inplace=True)

        logger.debug(
            "Read a data-frame",
            project=self.project,
            endpoint_id=endpoint_id,
            is_empty=df.empty,
        )

        return df_handler(df=df, metrics=metrics, project=self.project)

        # for metric in metrics:
        #     full_name = _compose_full_name(project=self.project, app=metric.app, name=metric.name)
        #     if df.empty:
        #         results.append(
        #             mm_schemas.ModelEndpointMonitoringMetricNoData(
        #                 full_name=full_name,
        #                 type=mm_schemas.ModelEndpointMonitoringMetricType.METRIC,
        #             )
        #         )
        #     else:
        #         results.append(
        #             mm_schemas.ModelEndpointMonitoringMetricValues(
        #                 full_name=full_name,
        #                 values=list(zip(df._wend, df[metric])),
        #             )
        #         )
        #
        # full_name = _compose_full_name(project=self.project, endpoint_id=endpoint_id)
        #     if df.empty:
        #         results.append(
        #             mm_schemas.ModelEndpointMonitoringMetricNoData(
        #                 full_name=full_name,
        #                 type=mm_schemas.ModelEndpointMonitoringMetricType.METRIC,
        #             )
        #         )
        #     else:
        #         results.append(
        #             mm_schemas.ModelEndpointMonitoringMetricValues(
        #                 full_name=full_name,
        #                 values=list(zip(df._wend, df[metric])),
        #             )
        #         )


    def read_predictions(
        self,
        *,
        endpoint_id: str,
        start: str,
        end: str,
        aggregation_window: typing.Optional[str] = None,
            limit: typing.Optional[int] = None,
    ) -> typing.Union[
        mm_schemas.ModelEndpointMonitoringMetricValues,
        mm_schemas.ModelEndpointMonitoringMetricNoData,
    ]:
        print('[EYAL]: now in read predictions TDENGINE')
        if not aggregation_window:
            logger.warning(
                "Aggregation window is not provided, defaulting to 10 minute."
            )
            aggregation_window = "10m"

        df = self.get_records(
            table=mm_schemas.TDEngineSuperTables.PREDICTIONS,
            start=start,
            end=end,
            columns=["latency"],
            filter_query=f"endpoint_id='{endpoint_id}'",
            agg=["count"],
            interval=aggregation_window,
        )

        full_name = mlrun.model_monitoring.helpers.get_invocations_fqn(self.project)
        print('[EYAL]: full_name: ', full_name)
        if df.empty:
            return mm_schemas.ModelEndpointMonitoringMetricNoData(
                full_name=full_name,
                type=mm_schemas.ModelEndpointMonitoringMetricType.METRIC,
            )

        # df['time'] = pd.to_datetime(df['time'])
        # df.set_index('time', inplace=True)
        # groupby_df = df.groupby(pd.Grouper(freq=aggregation_window)).count()

        # print('[EYAL]: grouped df: ', groupby_df)
        df['_wend'] = pd.to_datetime('_wend')
        df.set_index('_wend', inplace=True)
        return mm_schemas.ModelEndpointMonitoringMetricValues(
            full_name=full_name,
            values=list(
                zip(
                    df.index,
                    df["count(latency)"],
                )
            ),  # pyright: ignore[reportArgumentType]
        )


        # frames_read_kwargs: dict[str, Union[str, int, None]] = {"aggregators": "count"}
        # if aggregation_window:
        #     frames_read_kwargs["step"] = aggregation_window
        #     frames_read_kwargs["aggregation_window"] = aggregation_window
        # if limit:
        #     frames_read_kwargs["limit"] = limit
        # df = self.get_records(
        #     table=mm_schemas.FileTargetKind.PREDICTIONS,
        #     start=start,
        #     end=end,
        #     columns=["latency"],
        #     filter_query=f"endpoint_id=='{endpoint_id}'",
        #     **frames_read_kwargs,
        # )
        #
        # full_name = get_invocations_fqn(self.project)
        #
        # if df.empty:
        #     return mm_schemas.ModelEndpointMonitoringMetricNoData(
        #         full_name=full_name,
        #         type=mm_schemas.ModelEndpointMonitoringMetricType.METRIC,
        #     )
        #
        # return mm_schemas.ModelEndpointMonitoringMetricValues(
        #     full_name=full_name,
        #     values=list(
        #         zip(
        #             df.index,
        #             df["count(latency)"],
        #         )
        #     ),  # pyright: ignore[reportArgumentType]
        # )

    def read_prediction_metric_for_endpoint_if_exists(
        self, endpoint_id: str
    ) -> typing.Optional[mm_schemas.ModelEndpointMonitoringMetric]:
        # Read just one record, because we just want to check if there is any data for this endpoint_id
        predictions = self.read_predictions(
            endpoint_id=endpoint_id, start="0", end="now", limit=1
        )
        if predictions:
            return mm_schemas.ModelEndpointMonitoringMetric(
                project=self.project,
                app=mm_schemas.SpecialApps.MLRUN_INFRA,
                type=mm_schemas.ModelEndpointMonitoringMetricType.METRIC,
                name=mm_schemas.PredictionsQueryConstants.INVOCATIONS,
                full_name=mlrun.model_monitoring.helpers.get_invocations_fqn(self.project),
            )
