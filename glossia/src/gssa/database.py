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
import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

from .definition import GoSmartSimulationDefinition


# SQLite database for storing information about active databases
class SQLiteSimulationDatabase:
    def __init__(self, database):
        # If we do not currently have a database file, we should make one
        should_create = not os.path.exists(database)

        self._db = sqlite3.connect(database)
        self._db.row_factory = sqlite3.Row

        if should_create:
            self.create()

    def updateValidation(self, guid, validation_xml):
        """Add the validation XML string to the simulation row."""
        cursor = self._db.cursor()
        cursor.execute('''
            UPDATE simulations
            SET validation=:validation
            WHERE guid=:guid
        ''', {'guid': guid, 'validation': validation_xml})
        self._db.commit()

    def getValidation(self, guid):
        """Return just the validation XML string for a simulation."""
        cursor = self._db.cursor()
        cursor.execute('''
            SELECT validation
            FROM simulations
            WHERE guid=? AND deleted=0
        ''', guid)
        try:
            simulation_row = cursor.fetchone()
        except Exception:
            return None

        validation = simulation_row[0]
        return validation

    def setStatus(self, guid, exit_code, status, percentage, timestamp):
        """Update the status of a simulation in the database."""
        cursor = self._db.cursor()
        cursor.execute('''
            UPDATE simulations
            SET exit_code=:exit_code, status=:status, percentage=:percentage, timestamp=:timestamp
            WHERE guid=:guid
            ''', {"guid": guid, "status": status, "percentage": percentage, "exit_code": exit_code, "timestamp": timestamp})
        self._db.commit()

    def getStatusAndValidation(self, guid):
        """Return both status and validation."""
        cursor = self._db.cursor()
        cursor.execute('''
            SELECT status, percentage, exit_code, timestamp, validation
            FROM simulations
            WHERE guid=? AND deleted=0
            ''', guid)
        try:
            simulation_row = cursor.fetchone()
        except Exception:
            return None

        status, percentage, exit_code, timestamp, validation = simulation_row
        return percentage, status, exit_code, timestamp, validation

    def create(self):
        """Set up the database."""
        cursor = self._db.cursor()
        cursor.execute('''
            CREATE TABLE simulations(
                id INTEGER PRIMARY KEY,
                guid TEXT UNIQUE,
                directory TEXT,
                exit_code TEXT NULLABLE DEFAULT NULL,
                status TEXT,
                percentage REAL,
                timestamp REAL,
                validation TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deleted TINYINT DEFAULT 0
                )
        ''')
        self._db.commit()

    def addOrUpdate(self, simulation):
        """Update the simulation row or add a new one if not already here."""
        try:
            cursor = self._db.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO simulations(guid, directory)
                VALUES(:guid,:directory)
            ''', {"guid": simulation.get_guid(), "directory": simulation.get_dir()})
            self._db.commit()
        except Exception:
            logging.exception("Problem inserting data into simulations")

    def markAllOld(self):
        """Mark simulations as exited if still appearing to run - usually on server start-up."""
        cursor = self._db.cursor()
        cursor.execute('''
            UPDATE simulations
            SET status=('Unfinished (' || percentage || '%)'), percentage=0, exit_code='E_UNKNOWN'
            WHERE percentage IS NOT NULL AND percentage < 100
        ''')
        self._db.commit()

    def active_count(self):
        """Check how many simulations are still marked IN_PROGRESS."""
        cursor = self._db.cursor()
        cursor.execute('''
            SELECT COUNT(id) as active
            FROM simulations
            WHERE status="IN_PROGRESS"
        ''')
        return cursor.fetchone()['active']

    def all(self):
        cursor = self._db.cursor()
        cursor.execute('''
            SELECT *
            FROM simulations
        ''')

        simulations = cursor.fetchall()
        return simulations

    def search(self, guid_start):
        cursor = self._db.cursor()
        guid = str(guid_start) + '%'
        cursor.execute('''
            SELECT *
            FROM simulations
            WHERE guid LIKE :guid AND deleted=0
        ''', {'guid': guid})
        try:
            simulation_rows = cursor.fetchall()
        except Exception:
            return None

        # Simulations should not be added to the database until they are finalized
        def buildsim(s):
            d = GoSmartSimulationDefinition(s['guid'], None, s['directory'], None, finalized=True)
            d._status = {'percentage': s['percentage'], 'message': s['status'], 'timestamp': s['timestamp']}
            d.set_exit_status(s['exit_code'], s['status'])
            return d

        simulations = {s['guid']: buildsim(s) for s in simulation_rows if os.path.exists(s['directory'])}

        return simulations

    def retrieve(self, guid):
        """Get a simulation by the client's GUID."""
        if len(guid) < 32:
            return self.search(guid)

        cursor = self._db.cursor()
        cursor.execute('''
            SELECT *
            FROM simulations
            WHERE guid=:guid AND deleted=0
        ''', {'guid': guid})
        try:
            simulation_row = cursor.fetchone()
        except Exception:
            return None

        if not simulation_row:
            return None

        directory = simulation_row['directory']

        if not os.path.exists(directory):
            return None

        # Simulations should not be added to the database until they are finalized
        return GoSmartSimulationDefinition(guid, None, directory, None, finalized=True)

    def delete(self, simulation, soft=True):
        """Remove a simulation from the database.

        Args:
            simulation (str): GUID of the simulation.
            soft (Optional[bool]): do a soft delete.

        """
        cursor = self._db.cursor()
        if soft:
            cursor.execute('''
                UPDATE simulations
                SET deleted=1
                WHERE guid=?
            ''', simulation.get_guid())
        else:
            cursor.execute('''
                DELETE FROM simulations
                WHERE guid=?
            ''', simulation.get_guid())

    def __del__(self):
        # Tidy up before we leave
        self._db.close()
