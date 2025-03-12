GitLab Injector

This script reads a YAML file defining a GitLab group structure and creates the corresponding
- groups
- subgroups
- projects
- epics
- issues
- labels

It could be useful to persons needing some data in GitLab in order to test their integration with this one.

## Usage

```bash
python gitlab_injector.py --config config.yaml --token YOUR_TOKEN --url https://gitlab.example.com [--group "parent/group/path"] [--debug]
```

## Command Line Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--config` | Yes | Path to the YAML configuration file that defines the GitLab structure |
| `--token` | Yes | GitLab personal access token with API access |
| `--url` | Yes | GitLab URL (e.g., https://gitlab.example.com) |
| `--group` | No | Parent group path where top-level groups should be created (e.g., "group/subgroup"). If not specified, groups will be created at the root level. |
| `--debug` | No | Enable debug logging for more detailed output |

## Example

To create a GitLab structure at the root level:
```bash
python gitlab_injector.py --config example.yaml --token YOUR_TOKEN --url https://gitlab.example.com
```

To create a GitLab structure within an existing group:
```bash
python gitlab_injector.py --config example.yaml --token YOUR_TOKEN --url https://gitlab.example.com --group "parent/group"
```
