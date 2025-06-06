# GitLab Injector

This script reads a YAML file defining a GitLab group structure and injects the corresponding
- groups
- subgroups
- projects
- epics
- issues
- labels
- iterations
- milestones

It could be useful to persons needing some data in GitLab in order to test their integration with this one.

## Prerequisites

Install the required Python packages:
```bash
pip install -r requirements.txt
```

## Usage

```bash
python gitlab_injector.py --config config.yaml --token YOUR_TOKEN --url https://gitlab.example.com [--group "parent/group/path"]
```

## Command Line Parameters

| Parameter  | Required | Description                                                                                                                                      |
|------------|----------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| `--config` | Yes      | Path to the YAML configuration file that defines the data to inject                                                                              |
| `--token`  | Yes      | GitLab personal access token with API write access                                                                                               |
| `--url`    | Yes      | GitLab URL (e.g., https://gitlab.example.com)                                                                                                    |
| `--group`  | No       | Parent group path where top-level groups should be created (e.g., "group/subgroup"). If not specified, groups will be created at the root level. |

## YAML file

The schema of the YAML file is defined in [schema.yaml](./schema.yaml).

The references between entities (e.g., to set a label on an issue) are using `id`s. A referenced entity must always be defined before the referring entity. Otherwise, the reference cannot be created; in this case, a warning `Xxx id='yyy' not found in xxx map` (e.g., `Label id='unknown' not found in label map`) is logged.  
If an `id` is used twice (i.e. used for two different entities), the second definition will be ignored, an error `Xxx id='{id}' is already mapped to '{GitLab ID}'` (e.g., `Milestone id='milestone2' is already mapped to '5948769'`) will be logged.

[example.yaml](./example.yaml) is a sample file.

## Example

To inject a group at the root level:
```bash
python gitlab_injector.py --config example.yaml --token YOUR_TOKEN --url https://gitlab.example.com
```

To inject a group within an existing group:
```bash
python gitlab_injector.py --config example.yaml --token YOUR_TOKEN --url https://gitlab.example.com --group "parent/group"
```

## Postscript

All this stuff has been written by Claude Sonnet and fixed by me.
