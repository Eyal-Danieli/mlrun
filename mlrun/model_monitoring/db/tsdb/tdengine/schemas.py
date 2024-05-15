import mlrun.common.schemas.model_monitoring as mm_constants
import mlrun.common.types

from dataclasses import dataclass


class TDEngineColumnType:
    def __init__(self, data_type: str, length: int = None):
        self.data_type = data_type
        self.length = length

    def __str__(self):
        if self.length is not None:
            return f"{self.data_type}({self.length})"
        else:
            return self.data_type


class TDEngineColumn(mlrun.common.types.StrEnum):
    TIMESTAMP = TDEngineColumnType("TIMESTAMP")
    FLOAT = TDEngineColumnType("FLOAT")
    INT = TDEngineColumnType("INT")
    BINARY_40 = TDEngineColumnType("BINARY", 40)
    BINARY_64 = TDEngineColumnType("BINARY", 64)
    BINARY_10000 = TDEngineColumnType("BINARY", 10000)


class TDEngineSchema:
    def __init__(self, table_name: str, columns: dict[str, str], tags: dict[str, str]):
        self.table_name = table_name
        self.columns = columns
        self.tags = tags

    def _create_super_table_query(self, db_prefix: str = "") -> str:
        columns = ", ".join(f"{col} {val}" for col, val in self.columns.items())
        tags = ", ".join(f"{col} {val}" for col, val in self.tags.items())
        return f"CREATE TABLE {db_prefix}{self.table_name} ({columns}) TAGS ({tags});"


@dataclass
class AppResultTable(TDEngineSchema):
    table_name: str = mm_constants.TDEngineSuperTables.APP_RESULTS
    columns = {
        mm_constants.WriterEvent.END_INFER_TIME: TDEngineColumn.TIMESTAMP,
        mm_constants.WriterEvent.START_INFER_TIME: TDEngineColumn.TIMESTAMP,
        mm_constants.ResultData.RESULT_VALUE: TDEngineColumn.FLOAT,
        mm_constants.ResultData.RESULT_STATUS: TDEngineColumn.INT,
        mm_constants.ResultData.RESULT_KIND: TDEngineColumn.BINARY_40,
        mm_constants.ResultData.CURRENT_STATS: TDEngineColumn.BINARY_10000,
    }

    tags = {
        mm_constants.EventFieldType.PROJECT: TDEngineColumn.BINARY_64,
        mm_constants.WriterEvent.ENDPOINT_ID: TDEngineColumn.BINARY_64,
        mm_constants.WriterEvent.APPLICATION_NAME: TDEngineColumn.BINARY_64,
        mm_constants.ResultData.RESULT_NAME: TDEngineColumn.BINARY_64,
    }


@dataclass
class Metrics(TDEngineSchema):
    table_name: str = mm_constants.TDEngineSuperTables.METRICS
    columns = {
        mm_constants.WriterEvent.END_INFER_TIME: TDEngineColumn.TIMESTAMP,
        mm_constants.WriterEvent.START_INFER_TIME: TDEngineColumn.TIMESTAMP,
        mm_constants.MetricData.METRIC_VALUE: TDEngineColumn.FLOAT,
    }

    tags = {
        mm_constants.EventFieldType.PROJECT: TDEngineColumn.BINARY_64,
        mm_constants.WriterEvent.ENDPOINT_ID: TDEngineColumn.BINARY_64,
        mm_constants.WriterEvent.APPLICATION_NAME: TDEngineColumn.BINARY_64,
        mm_constants.MetricData.METRIC_NAME: TDEngineColumn.BINARY_64,
    }


@dataclass
class Predictions(TDEngineSchema):
    table_name: str = mm_constants.TDEngineSuperTables.PREDICTIONS
    columns = {
        mm_constants.EventFieldType.TIMESTAMP: TDEngineColumn.TIMESTAMP,
        mm_constants.EventFieldType.LATENCY: TDEngineColumn.FLOAT,
        mm_constants.EventKeyMetrics.CUSTOM_METRICS: TDEngineColumn.BINARY_10000,
    }
    tags = {
        mm_constants.EventFieldType.PROJECT: TDEngineColumn.BINARY_64,
        mm_constants.WriterEvent.ENDPOINT_ID: TDEngineColumn.BINARY_64,
    }

