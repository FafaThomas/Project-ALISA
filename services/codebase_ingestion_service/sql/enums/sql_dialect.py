from enum import Enum


class SQLDialect(str, Enum):

    ANSI = "ansi"

    POSTGRESQL = "postgresql"

    MSSQL = "mssql"

    MYSQL = "mysql"

    SQLITE = "sqlite"

    ORACLE = "oracle"