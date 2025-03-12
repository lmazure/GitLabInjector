import argparse
import logging
import sys
import time
from typing import Dict, List, Optional, Any, Tuple

import gitlab
import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("gitlab-creator")

class GitLabInjector:
    """
    Class to handle the creation of GitLab structures from YAML definitions.
    """

    def __init__(self, gitlab_url: str, private_token: str):
        """
        Initialize with GitLab connection parameters.
        
        Args:
            gitlab_url: URL of the GitLab instance
            private_token: Personal Access Token with API access
        """
        self.gl = gitlab.Gitlab(url=gitlab_url, private_token=private_token)
        self.gl.auth()
        logger.info(f"Connected to GitLab as {self.gl.user.username}")
        
        # ID mappings to track created entities
        self.label_id_map = {}  # Maps YAML label IDs to GitLab label IDs
        self.epic_id_map = {}   # Maps YAML epic IDs to GitLab epic IDs
        self.issue_id_map = {}  # Maps YAML issue IDs to GitLab issue IDs
        self.group_path_map = {}  # Maps group name to full path
    
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
            
            # Process top-level groups
            for group_data in data.get('groups', []):
                self.process_group(group_data)
            
            # Process relationships (after all entities are created)
            logger.info("Creating relationships between entities...")
            self.create_relationships(data)
            
            logger.info("YAML processing completed successfully!")
            
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML file: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"An error occurred: {e}")
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
                    logger.info(f"Subgroup already exists: {full_path}")
                except gitlab.exceptions.GitlabGetError:
                    # Create subgroup
                    group = parent_group.subgroups.create({
                        'name': group_name,
                        'path': group_path,
                        'description': group_desc,
                        'visibility': 'private'  # Adjust as needed
                    })
                    logger.info(f"Created subgroup: {full_path}")
            else:
                try:
                    group = self.gl.groups.get(group_path)
                    logger.info(f"Top-level group already exists: {group_path}")
                except gitlab.exceptions.GitlabGetError:
                    # Create top-level group
                    group = self.gl.groups.create({
                        'name': group_name,
                        'path': group_path,
                        'description': group_desc,
                        'visibility': 'private'  # Adjust as needed
                    })
                    logger.info(f"Created top-level group: {group_path}")
            
            # Store group path for later reference
            self.group_path_map[group_name] = group.full_path
            
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
            
        except gitlab.exceptions.GitlabCreateError as e:
            logger.error(f"Error creating group {group_name}: {e}")
            raise
    
    def process_label(self, label_data: Dict[str, Any], group_or_project: Any) -> int:
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
                    self.label_id_map[label_id] = existing_label.id
                    return existing_label.id
            except (gitlab.exceptions.GitlabGetError, StopIteration):
                existing_label = None
            
            # Create label if it doesn't exist
            if not existing_label:
                label = labels_manager.create({
                    'name': label_name,
                    'color': label_color,
                    'description': label_desc
                })
                logger.info(f"Created label: {label_name}")
                self.label_id_map[label_id] = label.id
                return label.id
                
        except gitlab.exceptions.GitlabCreateError as e:
            logger.error(f"Error creating label {label_name}: {e}")
            raise
    
    def process_epic(self, epic_data: Dict[str, Any], group: Any) -> int:
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
            logger.warning(f"Group {group.name} doesn't support epics (requires GitLab Premium/Ultimate)")
            return None
        
        epic_id = epic_data.get('id')
        epic_title = epic_data.get('title')
        epic_desc = epic_data.get('description', '')
        epic_state = epic_data.get('state', 'opened')
        
        try:
            # Search for existing epic by title
            existing_epics = list(group.epics.list(search=epic_title))
            existing_epic = next((e for e in existing_epics if e.title == epic_title), None)
            
            if existing_epic:
                logger.info(f"Epic already exists: {epic_title}")
                self.epic_id_map[epic_id] = existing_epic.id
                return existing_epic.id
            
            # Create epic if it doesn't exist
            epic = group.epics.create({
                'title': epic_title,
                'description': epic_desc,
                'state': epic_state
            })
            logger.info(f"Created epic: {epic_title}")
            self.epic_id_map[epic_id] = epic.id
            return epic.id
                
        except gitlab.exceptions.GitlabCreateError as e:
            logger.error(f"Error creating epic {epic_title}: {e}")
            raise
    
    def process_project(self, project_data: Dict[str, Any], group: Any) -> int:
        """
        Process and create a project.
        
        Args:
            project_data: Dictionary containing project definition
            group: GitLab group object
            
        Returns:
            The ID of the created/found project
        """
        project_name = project_data.get('name')
        project_desc = project_data.get('description', '')
        
        # Project path must be URL friendly
        project_path = project_name.lower().replace(' ', '-')
        
        try:
            # Check if project exists in group
            existing_projects = list(group.projects.list(search=project_name))
            existing_project = next((p for p in existing_projects if p.name == project_name), None)
            
            if existing_project:
                logger.info(f"Project already exists: {project_name}")
                project = self.gl.projects.get(existing_project.id)
            else:
                # Create project if it doesn't exist
                project = group.projects.create({
                    'name': project_name,
                    'description': project_desc,
                    'visibility': 'private',  # Adjust as needed
                    'initialize_with_readme': True
                })
                logger.info(f"Created project: {project_name}")
                
                # Give GitLab some time to initialize the project
                time.sleep(1)
                
                # Reload project to ensure all attributes are available
                project = self.gl.projects.get(project.id)
            
            # Process issues in project
            for issue_data in project_data.get('issues', []):
                self.process_issue(issue_data, project)
            
            return project.id
                
        except gitlab.exceptions.GitlabCreateError as e:
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
        
        try:
            # Search for existing issue by title
            existing_issues = list(project.issues.list(search=issue_title))
            existing_issue = next((i for i in existing_issues if i.title == issue_title), None)
            
            if existing_issue:
                logger.info(f"Issue already exists: {issue_title}")
                self.issue_id_map[issue_id] = existing_issue.id
                return existing_issue.id
            
            # Create issue if it doesn't exist
            issue = project.issues.create({
                'title': issue_title,
                'description': issue_desc
            })
            logger.info(f"Created issue: {issue_title}")
            
            # Update issue state if needed
            if issue_state == 'closed' and issue.state != 'closed':
                issue.state_event = 'close'
                issue.save()
                logger.info(f"Closed issue: {issue_title}")
            
            self.issue_id_map[issue_id] = issue.id
            return issue.id
                
        except gitlab.exceptions.GitlabCreateError as e:
            logger.error(f"Error creating issue {issue_title}: {e}")
            raise
    
    def create_relationships(self, data: Dict[str, Any]):
        """
        Create relationships between entities after all have been created.
        
        Args:
            data: The full YAML data structure
        """
        # Process epic parent relationships
        for group_data in data.get('groups', []):
            self._process_group_relationships(group_data)
    
    def _process_group_relationships(self, group_data: Dict[str, Any]):
        """
        Process relationships for a group and its children.
        
        Args:
            group_data: Dictionary containing group definition
        """
        # Process epic relationships
        for epic_data in group_data.get('epics', []):
            self._process_epic_relationships(epic_data)
        
        # Process project issue relationships
        for project_data in group_data.get('projects', []):
            for issue_data in project_data.get('issues', []):
                self._process_issue_relationships(issue_data)
        
        # Process subgroups recursively
        for subgroup_data in group_data.get('subgroups', []):
            self._process_group_relationships(subgroup_data)
    
    def _process_epic_relationships(self, epic_data: Dict[str, Any]):
        """
        Process relationships for an epic.
        
        Args:
            epic_data: Dictionary containing epic definition
        """
        epic_id = epic_data.get('id')
        parent_epic_id = epic_data.get('parent_epic_id')
        label_ids = epic_data.get('label_ids', [])
        
        # Skip if epic wasn't created successfully
        if epic_id not in self.epic_id_map:
            logger.warning(f"Skipping relationships for epic {epic_id} - not found in ID map")
            return
        
        gitlab_epic_id = self.epic_id_map[epic_id]
        
        try:
            # Get the epic object
            group_id = self._get_group_id_for_epic(gitlab_epic_id)
            if not group_id:
                logger.warning(f"Cannot find group for epic {epic_id}")
                return
                
            group = self.gl.groups.get(group_id)
            epic = group.epics.get(gitlab_epic_id)
            
            # Set parent epic if specified
            if parent_epic_id and parent_epic_id in self.epic_id_map:
                parent_gitlab_epic_id = self.epic_id_map[parent_epic_id]
                try:
                    epic.parent_id = parent_gitlab_epic_id
                    epic.save()
                    logger.info(f"Set parent epic for {epic.title}")
                except gitlab.exceptions.GitlabUpdateError as e:
                    logger.error(f"Error setting parent epic: {e}")
            
            # Add labels to epic
            for label_id in label_ids:
                if label_id in self.label_id_map:
                    try:
                        # Get label name from GitLab using ID
                        label_obj = self._get_label_by_id(group, self.label_id_map[label_id])
                        if label_obj:
                            # Add label to epic by name
                            if not self._epic_has_label(epic, label_obj.name):
                                epic.labels = epic.labels + [label_obj.name]
                                epic.save()
                                logger.info(f"Added label {label_obj.name} to epic {epic.title}")
                    except Exception as e:
                        logger.error(f"Error adding label {label_id} to epic: {e}")
                else:
                    logger.warning(f"Label {label_id} not found in label map")
            
        except gitlab.exceptions.GitlabGetError as e:
            logger.error(f"Error getting epic {gitlab_epic_id}: {e}")
    
    def _process_issue_relationships(self, issue_data: Dict[str, Any]):
        """
        Process relationships for an issue.
        
        Args:
            issue_data: Dictionary containing issue definition
        """
        issue_id = issue_data.get('id')
        parent_epic_id = issue_data.get('parent_epic_id')
        label_ids = issue_data.get('label_ids', [])
        
        # Skip if issue wasn't created successfully
        if issue_id not in self.issue_id_map:
            logger.warning(f"Skipping relationships for issue {issue_id} - not found in ID map")
            return
        
        gitlab_issue_id = self.issue_id_map[issue_id]
        
        try:
            # Get the issue object
            issue = self.gl.issues.get(gitlab_issue_id)
            project = self.gl.projects.get(issue.project_id)
            
            # Link to parent epic if specified (requires GitLab Premium/Ultimate)
            if parent_epic_id and parent_epic_id in self.epic_id_map:
                parent_gitlab_epic_id = self.epic_id_map[parent_epic_id]
                try:
                    # This API endpoint might require GitLab Premium/Ultimate
                    project.issues.get(issue.iid).link(
                        target_project_id=issue.project_id,
                        target_issue_iid=issue.iid,
                        link_type='relates_to'
                    )
                    logger.info(f"Linked issue {issue.title} to epic")
                except Exception as e:
                    logger.warning(f"Error linking issue to epic (may require Premium/Ultimate): {e}")
            
            # Add labels to issue
            for label_id in label_ids:
                if label_id in self.label_id_map:
                    try:
                        # Get label name from GitLab using ID
                        label_obj = self._get_label_by_id(project, self.label_id_map[label_id])
                        if label_obj:
                            # Add label to issue by name
                            if not self._issue_has_label(issue, label_obj.name):
                                issue.labels = issue.labels + [label_obj.name]
                                issue.save()
                                logger.info(f"Added label {label_obj.name} to issue {issue.title}")
                    except Exception as e:
                        logger.error(f"Error adding label {label_id} to issue: {e}")
                else:
                    logger.warning(f"Label {label_id} not found in label map")
            
        except gitlab.exceptions.GitlabGetError as e:
            logger.error(f"Error getting issue {gitlab_issue_id}: {e}")
    
    def _get_group_id_for_epic(self, epic_id: int) -> Optional[int]:
        """
        Find the group ID that contains a given epic.
        
        Args:
            epic_id: GitLab epic ID
            
        Returns:
            Group ID if found, None otherwise
        """
        # This is a simplified approach - in a real implementation, you might need
        # to query all groups to find the one containing the epic
        for group in self.gl.groups.list(all=True):
            try:
                if hasattr(group, 'epics'):
                    epic = group.epics.get(epic_id)
                    return group.id
            except gitlab.exceptions.GitlabGetError:
                continue
        return None
    
    def _get_label_by_id(self, group_or_project: Any, label_id: int) -> Optional[Any]:
        """
        Get a label object by its ID.
        
        Args:
            group_or_project: GitLab group or project object
            label_id: GitLab label ID
            
        Returns:
            Label object if found, None otherwise
        """
        try:
            if hasattr(group_or_project, 'labels'):
                for label in group_or_project.labels.list(all=True):
                    if label.id == label_id:
                        return label
        except Exception as e:
            logger.error(f"Error getting label {label_id}: {e}")
        return None
    
    def _epic_has_label(self, epic: Any, label_name: str) -> bool:
        """
        Check if an epic already has a given label.
        
        Args:
            epic: GitLab epic object
            label_name: Label name to check
            
        Returns:
            True if epic has the label, False otherwise
        """
        return label_name in epic.labels
    
    def _issue_has_label(self, issue: Any, label_name: str) -> bool:
        """
        Check if an issue already has a given label.
        
        Args:
            issue: GitLab issue object
            label_name: Label name to check
            
        Returns:
            True if issue has the label, False otherwise
        """
        return label_name in issue.labels

def main():
    """
    Main entry point for the script.
    """
    parser = argparse.ArgumentParser(description='Create GitLab structure from YAML definition')
    parser.add_argument('--config', required=True, help='Path to YAML configuration file')
    parser.add_argument('--token', required=True, help='GitLab personal access token')
    parser.add_argument('--url', required=True, help='GitLab URL (e.g., https://gitlab.example.com)')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    args = parser.parse_args()
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")
    
    creator = GitLabInjector(gitlab_url=args.url, private_token=args.token)
    creator.process_yaml(args.config)

if __name__ == '__main__':
    main()