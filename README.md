GitLab Injector

This script reads a YAML file defining a GitLab group structure and creates the corresponding
- groups
- subgroups
- projects
- epics
- issues
- labels

It could be useful to persons needing some data in GitLab in order to test their integration with this one.

Usage:
```bash
python gitlab_injector.py --config config.yaml --token YOUR_TOKEN --url https://gitlab.example.com
```
