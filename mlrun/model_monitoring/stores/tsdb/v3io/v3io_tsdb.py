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
from typing import Any
from mlrun.model_monitoring.stores.tsdb.tsdb import TSDBstore
from mlrun.common.schemas.model_monitoring import (
    EventFieldType,
    EventKeyMetrics,
    AppResultEvent,
    WriterEvent,
)
from .stream_steps import ProcessBeforeTSDB, FilterAndUnpackKeys
import os
import datetime
import pandas as pd
from mlrun.utils import logger
from v3io_frames.errors import Error as V3IOFramesError
from v3io_frames.client import ClientBase as V3IOFramesClient
from v3io.dataplane import Client as V3IOClient
import mlrun.utils.v3io_clients
from v3io_frames.frames_pb2 import IGNORE
import json
_TSDB_BE = "tsdb"
_TSDB_RATE = "1/s"


class V3IOTSDBstore(TSDBstore):
    def __init__(
        self,
        project: str,
        access_key: str = None,
        table: str = None,
        container: str = None,
        v3io_framesd: str = None,
        create_table: bool = False,
    ):
        super().__init__(project=project)
        # Initialize a V3IO client instance
        self.access_key = access_key or os.environ.get("V3IO_ACCESS_KEY")
        self._v3io_client: V3IOClient = mlrun.utils.v3io_clients.get_v3io_client(
            endpoint=mlrun.mlconf.v3io_api,
        )

        self.table = table
        self.container = container

        self.v3io_framesd = v3io_framesd or mlrun.mlconf.v3io_framesd
        self._frames_client: V3IOFramesClient = self._get_v3io_frames_client(
            self.container
        )


        if create_table:
            self._create_tsdb_table()

    @staticmethod
    def _get_v3io_frames_client(v3io_container: str) -> V3IOFramesClient:
        return mlrun.utils.v3io_clients.get_frames_client(
            address=mlrun.mlconf.v3io_framesd,
            container=v3io_container,
        )

    def apply_monitoring_stream_steps(
        self,
        graph,
        tsdb_batching_max_events: int = 10,
        tsdb_batching_timeout_secs: int = 300,
    ):
        # Step 12 - Before writing data to TSDB, create dictionary of 2-3 dictionaries that contains
        # stats and details about the events
        print("[EYAL]: going to apply monitoring steps!")

        def apply_process_before_tsdb():
            graph.add_step(
                "ProcessBeforeTSDB", name="ProcessBeforeTSDB", after="sample"
            )

        apply_process_before_tsdb()

        # Steps 13-19: - Unpacked keys from each dictionary and write to TSDB target
        def apply_filter_and_unpacked_keys(name, keys):
            graph.add_step(
                "FilterAndUnpackKeys",
                name=name,
                after="ProcessBeforeTSDB",
                keys=[keys],
            )

        def apply_tsdb_target(name, after):
            graph.add_step(
                "storey.TSDBTarget",
                name=name,
                after=after,
                path=self.table,
                rate="10/m",
                time_col=EventFieldType.TIMESTAMP,
                container=self.container,
                access_key=self.access_key,
                v3io_frames=self.v3io_framesd,
                infer_columns_from_data=True,
                index_cols=[
                    EventFieldType.ENDPOINT_ID,
                    EventFieldType.RECORD_TYPE,
                    EventFieldType.ENDPOINT_TYPE,
                ],
                max_events=tsdb_batching_max_events,
                flush_after_seconds=tsdb_batching_timeout_secs,
                key=EventFieldType.ENDPOINT_ID,
            )

        # Steps 13-14 - unpacked base_metrics dictionary
        apply_filter_and_unpacked_keys(
            name="FilterAndUnpackKeys1",
            keys=EventKeyMetrics.BASE_METRICS,
        )
        apply_tsdb_target(name="tsdb1", after="FilterAndUnpackKeys1")

        # Steps 15-16 - unpacked endpoint_features dictionary
        apply_filter_and_unpacked_keys(
            name="FilterAndUnpackKeys2",
            keys=EventKeyMetrics.ENDPOINT_FEATURES,
        )
        apply_tsdb_target(name="tsdb2", after="FilterAndUnpackKeys2")

        # Steps 17-19 - unpacked custom_metrics dictionary. In addition, use storey.Filter remove none values
        apply_filter_and_unpacked_keys(
            name="FilterAndUnpackKeys3",
            keys=EventKeyMetrics.CUSTOM_METRICS,
        )

        def apply_storey_filter():
            graph.add_step(
                "storey.Filter",
                "FilterNotNone",
                after="FilterAndUnpackKeys3",
                _fn="(event is not None)",
            )

        apply_storey_filter()
        apply_tsdb_target(name="tsdb3", after="FilterNotNone")

    def write_application_event(self, event: AppResultEvent):
        event = AppResultEvent(event.copy())
        event[WriterEvent.END_INFER_TIME] = datetime.datetime.fromisoformat(
            event[WriterEvent.END_INFER_TIME]
        )
        del event[WriterEvent.RESULT_EXTRA_DATA]
        try:
            self._frames_client.write(
                backend=_TSDB_BE,
                table=self.table,
                dfs=pd.DataFrame.from_records([event]),
                index_cols=[
                    WriterEvent.END_INFER_TIME,
                    WriterEvent.ENDPOINT_ID,
                    WriterEvent.APPLICATION_NAME,
                    WriterEvent.RESULT_NAME,
                ],
            )
            logger.info("Updated V3IO TSDB successfully", table=self.table)
        except V3IOFramesError as err:
            logger.warn(
                "Could not write drift measures to TSDB",
                err=err,
                table=self.table,
                event=event,
            )

    def _create_tsdb_table(self) -> None:
        self._frames_client.create(
            backend=_TSDB_BE,
            table=self.table,
            if_exists=IGNORE,
            rate=_TSDB_RATE,
        )

    def update_default_data_drift(
        self,
        endpoint_id: str,
        drift_status: mlrun.common.schemas.model_monitoring.DriftStatus,
        drift_measure: float,
        drift_result: dict[str, dict[str, Any]],
        timestamp: pd.Timestamp,
        stream_container: str,
        stream_path: str,
    ):
        """Update drift results in input stream.

        :param endpoint_id:   The unique id of the model endpoint.
        :param drift_status:  Drift status result. Possible values can be found under DriftStatus enum class.
        :param drift_measure: The drift result (float) based on the mean of the Total Variance Distance and the
                              Hellinger distance.
        :param drift_result:  A dictionary that includes the drift results for each feature.
        :param timestamp:     Pandas Timestamp value.

        """

        if (
            drift_status
            == mlrun.common.schemas.model_monitoring.DriftStatus.POSSIBLE_DRIFT
            or drift_status
            == mlrun.common.schemas.model_monitoring.DriftStatus.DRIFT_DETECTED
        ):
            self._v3io_client.stream.put_records(
                container=stream_container,
                stream_path=stream_path,
                records=[
                    {
                        "data": json.dumps(
                            {
                                "endpoint_id": endpoint_id,
                                "drift_status": drift_status.value,
                                "drift_measure": drift_measure,
                                "drift_per_feature": {**drift_result},
                            }
                        )
                    }
                ],
            )

        # Update the results in tsdb:
        tsdb_drift_measures = {
            "endpoint_id": endpoint_id,
            "timestamp": timestamp,
            "record_type": "drift_measures",
            "tvd_mean": drift_result["tvd_mean"],
            "kld_mean": drift_result["kld_mean"],
            "hellinger_mean": drift_result["hellinger_mean"],
        }

        try:
            self._frames_client.write(
                backend="tsdb",
                table=self.table,
                dfs=pd.DataFrame.from_records([tsdb_drift_measures]),
                index_cols=["timestamp", "endpoint_id", "record_type"],
            )
        except V3IOFramesError as err:
            logger.warn(
                "Could not write drift measures to TSDB",
                err=err,
                tsdb_path=self.table,
                endpoint=endpoint_id,
            )

