# Copyright 2023 Iguazio
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


import mlrun.common.model_monitoring.helpers
import mlrun.common.schemas
from mlrun.config import is_running_as_api


def get_stream_path(project: str = None):
    """Get stream path from the project secret. If wasn't set, take it from the system configurations"""

    stream_uri = mlrun.get_secret_or_env(
        mlrun.common.schemas.model_monitoring.ProjectSecretKeys.STREAM_PATH
    ) or mlrun.mlconf.get_model_monitoring_file_target_path(
        project=project,
        kind=mlrun.common.schemas.model_monitoring.FileTargetKind.STREAM,
        target="online",
    )

    return mlrun.common.model_monitoring.helpers.parse_monitoring_stream_path(
        stream_uri=stream_uri, project=project
    )


def get_connection_string(project: str = None, secret_provider: mlrun.common.schemas.secret.SecretProviderName = None):
    """Get endpoint store connection string from the project secret. If wasn't set, take it from the system
    configurations.

    :param project:         Project name.
    :param secret_provider: A secret provider which in this case is usually a callable function to handle the secret
                            in the API side.

    :return:                Valid SQL connection string.

    """


    if secret_provider:
        return (
            mlrun.get_secret_or_env(
                key=mlrun.common.schemas.model_monitoring.ProjectSecretKeys.ENDPOINT_STORE_CONNECTION,
                secret_provider=secret_provider,
                prefix=project
            )
            or mlrun.mlconf.model_endpoint_monitoring.endpoint_store_connection
        )
    return (
            mlrun.get_secret_or_env(
                key=mlrun.common.schemas.model_monitoring.ProjectSecretKeys.ENDPOINT_STORE_CONNECTION,
            )
            or mlrun.mlconf.model_endpoint_monitoring.endpoint_store_connection
    )

