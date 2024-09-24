remote_script_path = "/home/ubuntu/run_fmbench.sh"

# Define a dictionary for common AMIs and their corresponding usernames
AMI_USERNAME_MAP = {
    "ami-": "ec2-user",  # Amazon Linux AMIs start with 'ami-'
    "ubuntu": "ubuntu",  # Ubuntu AMIs contain 'ubuntu' in their name
}