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

import typing
from abc import ABC, abstractmethod
class TSDBstore(ABC):
    def __init__(self, project: str):
        """
        Initialize a new model endpoint target.

        :param project:             The name of the project.
        """
        self.project = project

    def apply_monitoring_stream_step(self, **kwargs):
        pass


