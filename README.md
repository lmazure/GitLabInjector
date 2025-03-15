# GitLab Injector

This script reads a YAML file defining a GitLab group structure and injects the corresponding
- groups
- subgroups
- projects
- epics
- issues
- labels

It could be useful to persons needing some data in GitLab in order to test their integration with this one.

## Usage

```bash
python gitlab_injector.py --config config.yaml --token YOUR_TOKEN --url https://gitlab.example.com [--group "parent/group/path"]
```

## Command Line Parameters

| Parameter  | Required | Description                                                                                                                                      |
|------------|----------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| `--config` | Yes      | Path to the YAML configuration file that defines the data to inject                                                                              |
| `--token`  | Yes      | GitLab personal access token with API access                                                                                                     |
| `--url`    | Yes      | GitLab URL (e.g., https://gitlab.example.com)                                                                                                    |
| `--group`  | No       | Parent group path where top-level groups should be created (e.g., "group/subgroup"). If not specified, groups will be created at the root level. |

## YAML file

The schema of the YAML file is defined in [schema.yaml](./schema.yaml).

The pointers between entities (e.g. to set a label on an issue) are using `id`s. The pointed entities must always be defined before the pointing entity.

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
