import os
import time
import json
import wget
import yaml
import urllib
import boto3
import base64
import logging
import asyncio
import paramiko
from utils import *
from constants import *
from scp import SCPClient
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from botocore.exceptions import NoCredentialsError, ClientError

executor = ThreadPoolExecutor()


instance_id_list = []
fmbench_config_map = []
fmbench_post_startup_script_map = []

logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",  # Define log message format
    handlers=[
        logging.FileHandler("multi_deploy_ec2.log"),  # Log to a file
        logging.StreamHandler(),  # Also log to console
    ],
)


async def execute_fmbench(instance, formatted_script, remote_script_path):
    """
    Asynchronous wrapper for deploying an instance using synchronous functions.
    """
    # Check for the startup completion flag

    startup_complete = await asyncio.get_event_loop().run_in_executor(
        executor, wait_for_flag, instance, 600, 30, "/tmp/startup_complete.flag"
    )

    if startup_complete:
        # Handle configuration file (download/upload) and get the remote path
        remote_config_path = await handle_config_file_async(instance)

        # Format the script with the remote config file path
        # Change this later to be a better implementation, right now it is bad.
        formatted_script = formatted_script.format(
            hf_token=hf_token, config_file=remote_config_path
        )

        print("Startup Script complete, executing fmbench now")

        # Upload and execute the script on the instance
        script_output = await asyncio.get_event_loop().run_in_executor(
            executor,
            upload_and_execute_script_invoke_shell,
            instance["hostname"],
            instance["username"],
            instance["key_file_path"],
            formatted_script,
            remote_script_path,
        )
        print(f"Script Output from {instance['hostname']}:\n{script_output}")

        # Check for the fmbench completion flag
        fmbench_complete = await asyncio.get_event_loop().run_in_executor(
            executor, wait_for_flag, instance, 1200, 30, "/tmp/fmbench_completed.flag"
        )

        if fmbench_complete:
            print("Fmbench Run successful, Getting the folders now")
            await asyncio.get_event_loop().run_in_executor(
                executor, check_and_retrieve_results_folder, instance, "output"
            )


async def multi_deploy_fmbench(instance_details, remote_script_path):
    tasks = []

    # Create a task for each instance
    for instance in instance_details:
        #Make this async as well?
        # Format the script with the specific config file
        logger.info(f"Instance Details are: {instance}")
        logger.info(f"Attempting to open bash script at {instance['post_startup_script']}")
        with open(instance['post_startup_script']) as file:
            bash_script = file.read()
        
        logger.info("Read Bash Script")
        logger.info(f"{bash_script}")

        # Create an async task for this instance
        tasks.append(execute_fmbench(instance, bash_script, remote_script_path))

    # Run all tasks concurrently
    await asyncio.gather(*tasks)


async def main():
    await multi_deploy_fmbench(instance_details, remote_script_path)


logger = logging.getLogger(__name__)

if __name__ == "__main__":
    config_data = load_yaml_file(yaml_file_path)
    logger.info(f"Loaded Config {config_data}")

    hf_token = config_data["aws"].get("hf_token")

    logger.info(f"Creating Security Groups. Skipping if they exist")
    if config_data["run_steps"]["security_group_creation"]:
        GROUP_NAME = config_data["security_group"].get("group_name")
        DESCRIPTION = config_data["security_group"].get("description", " ")
        VPC_ID = config_data["security_group"].get("vpc_id", "")
        try:
            sg_id = create_security_group(GROUP_NAME, DESCRIPTION, VPC_ID)
            logger.info(f"security group imported")
            if sg_id:
                # Add inbound rules if security group was created successfully
                authorize_inbound_rules(sg_id)
                logger.info(f"Inbound rules imported")
        except ClientError as e:
            logger.info(
                f"An error occurred while creating or getting the security group: {e}"
            )

    logger.info(f"Key Pair Groups. Skipping if they exist")
    if config_data["run_steps"]["key_pair_generation"]:
        PRIVATE_KEY_FNAME = config_data["key_pair_gen"]["key_pair_name"]
        private_key = create_key_pair(PRIVATE_KEY_FNAME)
    elif config_data["run_steps"]["key_pair_generation"] == False:
        KEY_PAIR_NAME = config_data["key_pair_gen"]["key_pair_name"]
        PRIVATE_KEY_FNAME = config_data["key_pair_gen"]["key_pair_fpath"]
        try:
            with open(f"{PRIVATE_KEY_FNAME}", "r") as file:
                private_key = file.read()
        except FileNotFoundError:
            print(f"File not found: {PRIVATE_KEY_FNAME}")
        except IOError as e:
            print(f"Error reading file {PRIVATE_KEY_FNAME}: {e}")

    for i in config_data["instances"]:
        logger.info(f"Instance list is as follows: {i}")

    logger.info(f"Deploying Ec2 Instances")
    if config_data["run_steps"]["deploy_ec2_instance"]:
        iam_arn = config_data["aws"]["iam_instance_profile_arn"]
        print(iam_arn)
        # WIP Parallelize This.
        for instance in config_data["instances"]:
            instance_type = instance["instance_type"]
            ami_id = instance["ami_id"]
            startup_script = instance["startup_script"]
            # command_to_run = instance["command_to_run"]
            with open(f"{startup_script}", "r") as file:
                user_data_script = file.read()
            # user_data_script += command_to_run
            # Create an EC2 instance with the user data script
            instance_id = create_ec2_instance(
                KEY_PAIR_NAME,
                sg_id,
                user_data_script,
                ami_id,
                instance_type,
                iam_arn,
            )

            instance_id_list.append(instance_id)
            fmbench_config_map.append({instance_id: instance["fmbench_config"]})
            fmbench_post_startup_script_map.append({instance_id: instance['post_startup_script']})

    logger.info("Going to Sleep for 60 seconds to make sure the instances are up")
    time.sleep(60)

    if config_data["run_steps"]["run_bash_script"]:
        instance_details = generate_instance_details(
            instance_id_list, PRIVATE_KEY_FNAME, fmbench_config_map, fmbench_post_startup_script_map, region="us-east-1"
        )  # Call the async function
        asyncio.run(main())

    if config_data["run_steps"]["delete_ec2_instance"]:
        for instance_id in instance_id_list:
            delete_ec2_instance(instance_id)
        instance_id_list = []
