from datetime import datetime, timedelta

from airflow import DAG
from airflow.contrib.operators.gcp_sql_operator import CloudSqlInstanceExportOperator
from airflow.contrib.operators.gcs_to_bq import GoogleCloudStorageToBigQueryOperator
from airflow.contrib.operators.gcs_to_gcs import GoogleCloudStorageToGoogleCloudStorageOperator
from airflow.contrib.operators.bigquery_check_operator import BigQueryCheckOperator
from airflow.contrib.operators.bigquery_to_gcs import BigQueryToCloudStorageOperator
from airflow.contrib.operators.bigquery_operator import BigQueryOperator
from airflow.utils.dates import days_ago
from airflow.models import Variable
import os
import json

args = {
    'owner': 'packt-developer',
}

def read_json_schema(gcs_file_path):
    with open(gcs_file_path, "r") as f:
        schema_json = json.load(f)

    return schema_json

# Environment Variables
gcp_project_id = os.environ.get('GCP_PROJECT')
instance_name  = os.environ.get('MYSQL_INSTANCE_NAME')

# Airflow Variables
settings = Variable.get("level_3_dag_settings", deserialize_json=True)

# DAG Variables
gcs_source_data_bucket = settings['gcs_source_data_bucket']
bq_raw_dataset         = settings['bq_raw_dataset']
bq_dwh_dataset         = settings['bq_dwh_dataset']

# Macros
execution_date         = '{{ ds }}'

# Stations
station_source_object  = "chapter-4/stations/stations.csv"
sql_query              = "SELECT * FROM apps_db.stations"

export_body = {
    "exportContext": {
        "fileType": "csv",
        "uri": f"gs://{gcs_source_data_bucket}/{station_source_object}",
        "csvExportOptions":{
            "selectQuery": sql_query
        }
    }
}

bq_stations_table_name = "stations"
bq_stations_table_id    = f"{gcp_project_id}.{bq_raw_dataset}.{bq_stations_table_name}"
bq_stations_table_schema = read_json_schema("/home/airflow/gcs/data/schema/stations_schema.json")

# Regions
gcs_regions_source_object = "from-git/chapter-3/dataset/regions/regions.csv"
gcs_regions_target_object = "chapter-4/regions/regions.csv"
bq_regions_table_name     = "regions"
bq_regions_table_id      = f"{gcp_project_id}.{bq_raw_dataset}.{bq_regions_table_name}"
bq_regions_table_schema = read_json_schema("/home/airflow/gcs/data/schema/regions_schema.json")

# Trips
bq_temporary_extract_dataset_name = "temporary_staging"
bq_temporary_extract_table_name = "trips"
bq_temporary_table_id = f"{gcp_project_id}.{bq_temporary_extract_dataset_name}.{bq_temporary_extract_table_name}"

gcs_trips_source_object = "chapter-4/trips/trips.csv"
gcs_trips_source_uri=f"gs://{gcs_source_data_bucket}/{gcs_trips_source_object}"

bq_trips_table_name = "trips"
bq_trips_table_id      = f"{gcp_project_id}.{bq_raw_dataset}.{bq_trips_table_name}"
bq_trips_table_schema = read_json_schema("/home/airflow/gcs/data/schema/trips_schema.json")

# DWH
bq_fact_trips_daily_table_name = "facts_trips_daily"
bq_fact_trips_daily_table_id      = f"{gcp_project_id}.{bq_dwh_dataset}.{bq_fact_trips_daily_table_name}"

bq_dim_stations_table_name = "dim_stations"
bq_dim_stations_table_id      = f"{gcp_project_id}.{bq_dwh_dataset}.{bq_dim_stations_table_name}"

with DAG(
    dag_id='level_3_dag_parameters',
    default_args=args,
    schedule_interval='0 5 * * *',
    start_date=datetime(2018, 1, 1),
    end_date=datetime(2018, 1, 5)
) as dag:

    ### Load Station Table ###
    export_mysql_station = CloudSqlInstanceExportOperator(
        task_id='export_mysql_station',
        project_id=gcp_project_id,
        body=export_body,
        instance=instance_name
    )

    gcs_to_bq_station = GoogleCloudStorageToBigQueryOperator(
        task_id                             = "gcs_to_bq_station",
        bucket                              = gcs_source_data_bucket,
        source_objects                      = [station_source_object],
        destination_project_dataset_table   = bq_stations_table_id,
        schema_fields                       = bq_stations_table_schema,
        write_disposition                   = 'WRITE_TRUNCATE'
    )

    ### Load Region Table ###
    gcs_to_gcs_region = GoogleCloudStorageToGoogleCloudStorageOperator(
        task_id             = 'gcs_to_gcs_region',
        source_bucket       = gcs_source_data_bucket,
        source_object       = gcs_regions_source_object,
        destination_bucket  = gcs_source_data_bucket,
        destination_object  = gcs_regions_target_object
    )

    gcs_to_bq_region = GoogleCloudStorageToBigQueryOperator(
    task_id                             = "gcs_to_bq_region",
    bucket                              = gcs_source_data_bucket,
    source_objects                      = [gcs_regions_target_object],
    destination_project_dataset_table   = bq_regions_table_id,
    schema_fields                       = bq_regions_table_schema,
    write_disposition                   ='WRITE_TRUNCATE'
    )

    ### Load Trips Table ###
    bq_to_bq_temporary_trips = BigQueryOperator(
    task_id='bq_to_bq_temporary_trips',
    sql=f"""
        SELECT * FROM `bigquery-public-data.san_francisco_bikeshare.bikeshare_trips`
        WHERE DATE(start_date) = DATE('{execution_date}')
        """,
    use_legacy_sql=False,
    destination_dataset_table=bq_temporary_table_id,
    write_disposition='WRITE_TRUNCATE',
    create_disposition='CREATE_IF_NEEDED')

    bq_to_gcs_extract_trips = BigQueryToCloudStorageOperator(
    task_id='bq_to_gcs_extract_trips',
    source_project_dataset_table=bq_temporary_table_id,
    destination_cloud_storage_uris=[gcs_trips_source_uri],
    print_header=False,
    export_format='CSV')

    gcs_to_bq_trips = GoogleCloudStorageToBigQueryOperator(
    task_id                             = "gcs_to_bq_trips",
    bucket                              = gcs_source_data_bucket,
    source_objects                      = [gcs_trips_source_object],
    destination_project_dataset_table   = bq_trips_table_id,
    schema_fields                       = bq_trips_table_schema,
    write_disposition                   ='WRITE_APPEND'
    )

    ### Load DWH Tables ###
    dwh_fact_trips_daily  = BigQueryOperator(
        task_id                     = "dwh_fact_trips_daily",
        sql                         = f"""SELECT DATE(start_date) as trip_date,
                                      start_station_id,
                                      COUNT(trip_id) as total_trips,
                                      SUM(duration_sec) as sum_duration_sec,
                                      AVG(duration_sec) as avg_duration_sec
                                      FROM `{bq_trips_table_id}`
                                      WHERE DATE(start_date) = DATE('{execution_date}')
                                      GROUP BY trip_date, start_station_id""",
        destination_dataset_table   = bq_fact_trips_daily_table_id,
        write_disposition           = 'WRITE_APPEND',
        create_disposition          = 'CREATE_IF_NEEDED',
        use_legacy_sql              = False,
        priority                    = 'BATCH'
    )

    dwh_dim_stations  = BigQueryOperator(
        task_id                     = "dwh_dim_stations",
        sql                         = f"""SELECT station_id,
                                      stations.name as station_name,
                                      regions.name as region_name,
                                      capacity
                                      FROM `{bq_stations_table_id}` stations
                                      JOIN `{bq_regions_table_id}` regions
                                      ON stations.region_id = CAST(regions.region_id AS STRING)
                                      ;""",
        destination_dataset_table   = bq_dim_stations_table_id,
        write_disposition           = 'WRITE_TRUNCATE',
        create_disposition          = 'CREATE_IF_NEEDED',
        use_legacy_sql              = False,
        priority                    = 'BATCH'
    )

    ### BQ Row Count Checker ###
    bq_row_count_check_dwh_fact_trips_daily = BigQueryCheckOperator(
    task_id='bq_row_count_check_dwh_fact_trips_daily',
    sql=f"""
    select count(*) from `{bq_fact_trips_daily_table_id}`
    """,
    use_legacy_sql=False)

    bq_row_count_check_dwh_dim_stations = BigQueryCheckOperator(
    task_id='bq_row_count_check_dwh_dim_stations',
    sql=f"""
    select count(*) from `{bq_dim_stations_table_id}`
    """,
    use_legacy_sql=False)

    ### Load Data Mart ###
    export_mysql_station >> gcs_to_bq_station
    gcs_to_gcs_region >> gcs_to_bq_region
    bq_to_bq_temporary_trips >> bq_to_gcs_extract_trips >> gcs_to_bq_trips

    [gcs_to_bq_station,gcs_to_bq_region,gcs_to_bq_trips] >> dwh_fact_trips_daily >> bq_row_count_check_dwh_fact_trips_daily
    [gcs_to_bq_station,gcs_to_bq_region,gcs_to_bq_trips] >> dwh_dim_stations >> bq_row_count_check_dwh_dim_stations

if __name__ == "__main__":
    dag.cli()