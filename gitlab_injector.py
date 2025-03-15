import argparse
import logging
import sys
import time
import os
import traceback
from typing import Dict, List, Optional, Any, Tuple

import gitlab
import yaml
import jsonschema

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("GitLabInjector")

class GitLabInjector:
    """
    Class to handle the creation of GitLab structures from YAML definitions.
    """

    def __init__(self, gitlab_url: str, private_token: str, parent_group_path: Optional[str] = None):
        """
        Initialize with GitLab connection parameters.
        
        Args:
            gitlab_url: URL of the GitLab instance
            private_token: Personal Access Token with API access
            parent_group_path: Optional path to parent group where top-level groups should be created
        """
        self.gl = gitlab.Gitlab(url=gitlab_url, private_token=private_token)
        self.gl.auth()
        logger.info(f"Connected to GitLab as {self.gl.user.username if self.gl.user else 'unknown'}")
        
        # Store parent group ID if provided
        self.parent_group_id = None
        if parent_group_path:
            try:
                parent_group = self.gl.groups.get(parent_group_path)
                self.parent_group_id = parent_group.id
                logger.info(f"Using parent group: {parent_group_path} (ID: {self.parent_group_id})")
            except gitlab.GitlabGetError:
                logger.error(f"Parent group not found: {parent_group_path}")
                sys.exit(1)
        
        self.label_name_map = {}  # Maps YAML label IDs to GitLab label names
        self.epic_id_map = {}     # Maps YAML epic IDs to GitLab epic IDs
        self.issue_id_map = {}    # Maps YAML issue IDs to GitLab issue IDs
    
    def process_yaml(self, yaml_file: str):
        """
        Process the YAML file and create the GitLab structure.
        
        Args:
            yaml_file: Path to the YAML file
        """
        try:
            with open(yaml_file, 'r') as f:
                data = yaml.safe_load(f)
        
            logger.info(f"Successfully loaded YAML file: {yaml_file}")
        
            # Load and validate against schema
            schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schema.yaml')
            with open(schema_path, 'r') as f:
                schema = yaml.safe_load(f)
        
            logger.info("Validating YAML against schema...")
            try:
                jsonschema.validate(instance=data, schema=schema)
                logger.info("YAML validation successful!")
            except jsonschema.ValidationError as e:
                logger.error(f"YAML validation failed: {e}")
                sys.exit(1)
        
            # Process top-level groups
            for group_data in data.get('groups', []):
                self.process_group(group_data, self.parent_group_id)
        
            # Process relationships (after all entities are created)
            #logger.info("Creating relationships between entities...")
            #self.create_relationships(data)
        
            logger.info("YAML processing completed successfully!")
        
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML file: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"An error occurred: {e}\n{traceback.format_exc()}")
            sys.exit(1)
    
    def process_group(self, group_data: Dict[str, Any], parent_id: Optional[int] = None) -> int:
        """
        Process and create a group or subgroup.
        
        Args:
            group_data: Dictionary containing group definition
            parent_id: ID of parent group (None for top-level groups)
            
        Returns:
            The ID of the created/found group
        """
        group_name = group_data.get('name')
        assert group_name, "Group name is missing"
        group_desc = group_data.get('description', '')
        
        # Group path must be URL friendly
        group_path = group_name.lower().replace(' ', '-')
        
        try:
            # Check if group exists
            if parent_id:
                parent_group = self.gl.groups.get(parent_id)
                full_path = f"{parent_group.full_path}/{group_path}"
                try:
                    group = self.gl.groups.get(full_path)
                    logger.info(f"Subgroup already exists: {full_path} (ID: {group.id})")
                except gitlab.GitlabGetError:
                    # Create subgroup
                    group = self.gl.groups.create({
                        'name': group_name,
                        'path': group_path,
                        'parent_id': parent_id,
                        'description': group_desc,
                        'visibility': 'private'  # Adjust as needed
                    })
                    logger.info(f"Created subgroup: {full_path} (ID: {group.id})")
            else:
                try:
                    group = self.gl.groups.get(group_path)
                    logger.info(f"Top-level group already exists: {group_path} (ID: {group.id})")
                except gitlab.GitlabGetError:
                    # Create top-level group
                    group = self.gl.groups.create({
                        'name': group_name,
                        'path': group_path,
                        'description': group_desc,
                        'visibility': 'private'  # Adjust as needed
                    })
                    logger.info(f"Created top-level group: {group_path} (ID: {group.id})")
            
            # Process labels at group level
            for label_data in group_data.get('labels', []):
                self.process_label(label_data, group)
            
            # Process epics at group level (only available with GitLab Premium/Ultimate)
            for epic_data in group_data.get('epics', []):
                self.process_epic(epic_data, group)
            
            # Process projects
            for project_data in group_data.get('projects', []):
                self.process_project(project_data, group)
            
            # Process subgroups (recursive)
            for subgroup_data in group_data.get('subgroups', []):
                self.process_group(subgroup_data, group.id)
            
            return group.id
            
        except gitlab.GitlabCreateError as e:
            logger.error(f"Error creating group {group_name}: {e}")
            raise
    
    def process_label(self, label_data: Dict[str, Any], group_or_project: Any) -> int|None:
        """
        Process and create a label.
        
        Args:
            label_data: Dictionary containing label definition
            group_or_project: GitLab group or project object
            
        Returns:
            The ID of the created/found label
        """
        label_id = label_data.get('id')
        label_name = label_data.get('name')
        label_color = label_data.get('color')
        label_desc = label_data.get('description', '')
        
        # Check if the parent is a group or project
        if hasattr(group_or_project, 'labels'):
            labels_manager = group_or_project.labels
        else:
            logger.error(f"Cannot create label on object: {group_or_project}")
            return None
        
        try:
            # Check if label exists
            try:
                # Using find instead of get as get may raise an error for multiple matches
                existing_labels = list(labels_manager.list(search=label_name))
                existing_label = next((l for l in existing_labels if l.name == label_name), None)
                
                if existing_label:
                    logger.info(f"Label already exists: {label_name}")
                    self.label_name_map[label_id] = label_name
                    return existing_label.id
            except (gitlab.GitlabGetError, StopIteration):
                existing_label = None
            
            # Create label if it doesn't exist
            if not existing_label:
                label = labels_manager.create({
                    'name': label_name,
                    'color': label_color,
                    'description': label_desc
                })
                logger.info(f"Created label: {label_name}")
                self.label_name_map[label_id] = label_name
                return label.id
                
        except gitlab.GitlabCreateError as e:
            logger.error(f"Error creating label {label_name}: {e}")
            raise
    
    def process_epic(self, epic_data: Dict[str, Any], group: Any) -> int|None:
        """
        Process and create an epic.
        
        Args:
            epic_data: Dictionary containing epic definition
            group: GitLab group object
            
        Returns:
            The ID of the created/found epic
        """
        # Skip if not Premium/Ultimate
        if not hasattr(group, 'epics'):
            logger.error(f"Cannot create epic on object: {group}")
            return None
        
        epic_id = epic_data.get('id')
        epic_title = epic_data.get('title')
        epic_desc = epic_data.get('description', '')
        epic_state = epic_data.get('state', 'opened')
        epic_labels = epic_data.get('labels', [])
        epic_epic_parent_id = epic_data.get('parent_epic_id')
        
        try:
            # Search for existing epic by title
            existing_epics = list(group.epics.list(search=epic_title))
            epic = next((e for e in existing_epics if e.title == epic_title), None)
            
            if epic:
                logger.info(f"Epic already exists: {epic_title} (ID: {epic.id})")
            else:
                # Create epic if it doesn't exist
                epic = group.epics.create({
                    'title': epic_title,
                    'description': epic_desc,
                    'state': epic_state
                })
                logger.info(f"Created epic: {epic_title} (ID: {epic.id})")
            self.epic_id_map[epic_id] = epic.id
            
            # Update epic state if needed
            if epic_state == 'closed' and epic.state != 'closed':
                epic.state_event = 'close'
                epic.save()
                logger.info(f"Closed epic: {epic_title} (ID: {epic.id})")
            
            # Add labels to epic
            for label_id in epic_labels:
                if label_id in self.label_name_map:
                    try:
                        epic.labels.append(self.label_name_map[label_id])
                        epic.save()
                        logger.info(f"Added label {label_id} (name: {self.label_name_map[label_id]}) to epic {epic.title}")
                    except Exception as e:
                        logger.error(f"Error adding label {label_id} to epic: {e}")
                else:
                    logger.warning(f"Label {label_id} not found in label map")
            
            # Set parent epic if provided
            if epic_epic_parent_id:
                parent_epic = self.epic_id_map.get(epic_epic_parent_id)
                if parent_epic:
                    epic.parent_epic_id = parent_epic
                    epic.save()
                    logger.info(f"Set parent epic {parent_epic} (ID: {parent_epic}) for epic {epic.title}")
                else:
                    logger.warning(f"Parent epic {epic_epic_parent_id} not found in epic map")

            return epic.id

        except gitlab.GitlabListError as e:
            if "403" in str(e):
                logger.warning(f"Error listing epics (may be due to missing a premium/ultimate license): {e}")
                return None
            logger.error(f"Error listing epics: {e}")
            raise
        except gitlab.GitlabCreateError as e:
            logger.error(f"Error creating epic {epic_title}: {e}")
            raise

    def process_project(self, project_data: Dict[str, Any], group: Any) -> int|None:
        """
        Process and create a project.
        
        Args:
            project_data: Dictionary containing project definition
            group: GitLab group object
            
        Returns:
            The ID of the created/found project
        """
        project_name = project_data.get('name')
        assert project_name, "Project name is missing"
        project_desc = project_data.get('description', '')
        
        try:
            # Check if project exists in group
            existing_projects = list(group.projects.list(search=project_name))
            existing_project = next((p for p in existing_projects if p.name == project_name), None)
            
            if existing_project:
                logger.info(f"Project already exists: {project_name} (ID: {existing_project.id})")
                project = self.gl.projects.get(existing_project.id)
            else:
                # Create project if it doesn't exist
                project = self.gl.projects.create({
                    'name': project_name,
                    'namespace_id': group.id,
                    'description': project_desc,
                    'visibility': 'private'  # Adjust as needed
                })
                logger.info(f"Created project: {project_name} (ID: {project.id})")
                
                # Give GitLab some time to initialize the project
                time.sleep(1)
                
                # Reload project to ensure all attributes are available
                project = self.gl.projects.get(project.id)
            
            # Process issues in project
            for issue_data in project_data.get('issues', []):
                self.process_issue(issue_data, project)
            
            return project.id
                
        except gitlab.GitlabCreateError as e:
            logger.error(f"Error creating project {project_name}: {e}")
            raise
    
    def process_issue(self, issue_data: Dict[str, Any], project: Any) -> int:
        """
        Process and create an issue.
        
        Args:
            issue_data: Dictionary containing issue definition
            project: GitLab project object
            
        Returns:
            The ID of the created/found issue
        """
        issue_id = issue_data.get('id')
        issue_title = issue_data.get('title')
        issue_desc = issue_data.get('description', '')
        issue_state = issue_data.get('state', 'opened')
        issue_labels = issue_data.get('labels', [])
        issue_parent_epic_id = issue_data.get('parent_epic_id')

        try:
            # Search for existing issue by title
            existing_issues = list(project.issues.list(search=issue_title))
            issue = next((i for i in existing_issues if i.title == issue_title), None)
            
            if issue:
                logger.info(f"Issue already exists: {issue_title} (ID: {issue.id})")
            else:
                # Create issue if it doesn't exist
                issue = project.issues.create({
                    'title': issue_title,
                    'description': issue_desc
                })
                logger.info(f"Created issue: {issue_title} (ID: {issue.id})")
            self.issue_id_map[issue_id] = issue.id

            # Update issue state if needed
            if issue_state == 'closed' and issue.state != 'closed':
                issue.state_event = 'close'
                issue.save()
                logger.info(f"Closed issue: {issue_title} (ID: {issue.id})")
            
            # Add labels to issue
            for label_id in issue_labels:
                if label_id in self.label_name_map:
                    try:
                        issue.labels.append(self.label_name_map[label_id])
                        issue.save()
                        logger.info(f"Added label {label_id} (name: {self.label_name_map[label_id]}) to issue {issue.title}")
                    except Exception as e:
                        logger.error(f"Error adding label {label_id} to issue: {e}")
                else:
                    logger.warning(f"Label {label_id} not found in label map")

            # Set parent epic if provided
            if issue_parent_epic_id:
                parent_epic = self.epic_id_map.get(issue_parent_epic_id)
                if parent_epic:
                    issue.parent_epic_id = parent_epic
                    issue.save()
                    logger.info(f"Set parent epic {parent_epic} (ID: {parent_epic}) for issue {issue.title}")
                else:
                    logger.warning(f"Parent epic {issue_parent_epic_id} not found in epic map")
            
            return issue.id

        except gitlab.GitlabCreateError as e:
            logger.error(f"Error creating issue {issue_title}: {e}")
            raise
    
def main():
    """
    Main entry point for the script.
    """
    parser = argparse.ArgumentParser(description='Create GitLab structure from YAML definition')
    parser.add_argument('--config', required=True, help='Path to YAML configuration file')
    parser.add_argument('--token', required=True, help='GitLab personal access token')
    parser.add_argument('--url', required=True, help='GitLab URL (e.g., https://gitlab.example.com)')
    parser.add_argument('--group', help='Parent group path where top-level groups should be created (e.g., "group/subgroup")')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    args = parser.parse_args()
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")
    
    creator = GitLabInjector(gitlab_url=args.url, private_token=args.token, parent_group_path=args.group)
    creator.process_yaml(args.config)

if __name__ == '__main__':
    main()