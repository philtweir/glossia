# This file is part of the Go-Smart Simulation Architecture (GSSA).
# Go-Smart is an EU-FP7 project, funded by the European Commission.
#
# Copyright (C) 2013-  NUMA Engineering Ltd. (see AUTHORS file)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
from gssa.family import Family
import gssa.parameters


import os
import lxml.etree
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

from gssa.families.gssf_arguments import GoSmartSimulationFrameworkArguments
from gssa.families.mesher_gssf import MesherGSSFMixin


class ElmerLibNumaLegacyFamily(Family, MesherGSSFMixin):
    family_name = "elmer-libnuma-legacy"

    _disallowed_functions = (
        "funcdel",
        "sprintf",
        "sscanf",
        "eval",
        "source",
        "fread",
        "fscanf",
        "fgets",
        "fwrite",
        "fprintf",
        "fputs",
        "fopen",
        "freopen",
        "fclose",
        "save",
        "load",
        "format"
    )

    _xml = None
    _validation_file = None

    def __init__(self, files_required):
        self._needles = {}
        self._needle_order = {}
        self._files_required = files_required
        self._args = GoSmartSimulationFrameworkArguments(configfilenames=["settings.xml"])

    def get_percentage_socket_location(self, working_directory):
        return os.path.join(working_directory, 'update.sock')

    # Needle index can be either needle index (as given in XML input) or an
    # integer n indicating the nth needle in the order of the needles XML block
    # FIXME: is this order guaranteed? in any case, non-id is DEPRECATED
    def get_needle_parameter(self, needle_index, key, try_json=True):
        if needle_index not in self._needles and needle_index in self._needle_order:
            needle_index = self._needle_order[needle_index]

        value = self.get_parameter(key, try_json, self._needles[needle_index]["parameters"])

        return value

    def get_parameter(self, key, try_json=True, parameters=None):
        if parameters is None:
            parameters = self._parameters

        if key not in parameters:
            return None

        parameter, typ = parameters[key]

        return gssa.parameters.convert_parameter(parameter, typ, try_json)

    @asyncio.coroutine
    def simulate(self, working_directory):
        try:
            translated_xml = self.to_xml()
        except RuntimeError as e:
            logger.error("Could not prepare simulation XML")
            raise e

        tree = lxml.etree.ElementTree(translated_xml)

        with open(os.path.join(working_directory, "settings.xml"), "wb") as f:
            tree.write(f, pretty_print=True)

        self._args.status_socket = self.get_percentage_socket_location(working_directory)
        args = ["go-smart-launcher"] + self._args.to_list()

        task = yield from asyncio.create_subprocess_exec(
            *[a for a in args if a not in ('stdin', 'stdout', 'stderr')],
            cwd=working_directory
        )

        yield from task.wait()

        self._simulation_directory = working_directory

        return task.returncode == 0

    @asyncio.coroutine
    def validation(self, working_directory=None):
        if not working_directory:
            working_directory = self._simulation_directory

        validation_file = os.path.join(working_directory, 'validation.xml')
        if not os.path.exists(validation_file):
            raise RuntimeError("Validation file not found: [%s]" % validation_file)

        with open(validation_file, 'r') as f:
            tree = lxml.etree.parse(f)
            root = tree.getroot()

            if root.tag != 'validation_struct':
                raise RuntimeError("Validation XML did not have a validation_struct root tag (found %s)" % root.tag)

            return json.dumps(dict([(b.tag, b.text) for b in root]))

    def retrieve_files(self, destination):
        pass

    @asyncio.coroutine
    def clean(self):
        return True

    def load_definition(self, xml, parameters, algorithms):
        self.load_core_definition(xml, parameters, algorithms)

    def to_xml(self):
        root = self.to_mesh_xml()

        elmer = lxml.etree.SubElement(root, 'elmer')
        sif = lxml.etree.SubElement(elmer, 'variant')
        sif.text = self._definition
        sif.text += "\n{{ p.SOURCES }}\n"

        modules = self.get_parameter('ELMER_NUMA_MODULES')
        if modules:
            sif.set("modules", "; ".join(modules))

        for ix, needle in self._needles.items():
            if needle['class'] == 'point-sources':
                point_sources = lxml.etree.SubElement(elmer, "pointsources")
                location = needle['file'].split(':', 1)

                extrapolated = False
                prong_locations = self.get_needle_parameter(ix, "NEEDLE_PRONGS_LOCATIONS")
                if location[0] == 'library':
                    if location[1] == 'straight tines' and prong_locations:
                        point_sources.set("system", "extrapolated")
                        extrapolated = True
                    else:
                        point_sources.set("system", location[1])
                else:
                    raise RuntimeError("Unknown point source distribution method: " + location[0])

                extensions = lxml.etree.SubElement(point_sources, "extensions")
                extension_lengths = self.get_parameter("CONSTANT_NEEDLE_EXTENSIONS")
                for phase, extension in enumerate(extension_lengths):
                    extension_node = lxml.etree.SubElement(extensions, "extension")
                    extension_node.set("phase", str(phase))
                    extension_node.set("length", str(extension))
                if extrapolated:
                    points = lxml.etree.SubElement(point_sources, "points")
                    for i, location in enumerate(prong_locations):
                        point = lxml.etree.SubElement(points, "point")
                        point.set('i', str(i))
                        for c, x in zip(('x', 'y', 'z'), location):
                            point.set(c, x)

        algorithms = lxml.etree.SubElement(elmer, 'algorithms')
        for result, definition in self._algorithms.items():
            algorithm = lxml.etree.SubElement(algorithms, "algorithm")
            algorithm.set("result", result)
            algorithm.set("arguments", ",".join(definition["arguments"]))
            arguments = lxml.etree.SubElement(algorithm, "arguments")
            for argument in sorted(definition["arguments"]):
                argument_node = lxml.etree.SubElement(arguments, "argument")
                argument_node.set("name", argument)

            content = lxml.etree.SubElement(algorithm, "content")
            content.text = definition["content"]
            if content.text is None:
                content.text = ''
            for fn in self._disallowed_functions:
                if fn in content.text or fn in result:
                    raise RuntimeError("Disallowed function appeared in algorithm %s" % result)

        lesion = lxml.etree.SubElement(root, 'lesion')
        lesion.set("field", self.get_parameter("SETTING_LESION_FIELD", False))

        threshold_upper = self.get_parameter("SETTING_LESION_THRESHOLD_UPPER")
        if threshold_upper is not None:
            lesion.set("threshold_upper", str(threshold_upper))

        threshold_lower = self.get_parameter("SETTING_LESION_THRESHOLD_LOWER")
        if threshold_lower is not None:
            lesion.set("threshold_lower", str(threshold_lower))

        segmented_lesions = dict([(n, r) for n, r in self._regions.items() if "segmented-lesions" in r["groups"]])
        if segmented_lesions:
            if len(segmented_lesions) > 1:
                raise RuntimeError("Too many segmented lesions (>1) for validation")
            validation = lxml.etree.SubElement(root, 'validation')
            validation.set('reference', next(iter(segmented_lesions.keys())))

        self._xml = root

        return self._xml
