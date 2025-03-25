import argparse
import logging
import sys
import time
import os
import traceback
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
import json

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
        if not self.gl.user:
            logger.error("No GitLab user associated to the provided token")
            sys.exit(1)
        self.current_user = self.gl.user.username
        logger.info(f"Connected to GitLab as {self.current_user}")
        
        self.gq = gitlab.GraphQL(gitlab_url, token=private_token)

        # Store parent group ID if provided
        self.parent_group_id = None
        if parent_group_path:
            try:
                parent_group = self.gl.groups.get(parent_group_path)
                self.parent_group_id = parent_group.id
                logger.info(f"Using parent group: {parent_group_path}")
            except gitlab.GitlabGetError:
                logger.error(f"Parent group not found: {parent_group_path}")
                sys.exit(1)
        
        self.label_name_map = {}    # Maps YAML label IDs to GitLab label names
        self.epic_id_map = {}       # Maps YAML epic IDs to GitLab epic IDs
        self.issue_id_map = {}      # Maps YAML issue IDs to GitLab issue IDs
        self.iteration_id_map = {}  # Maps YAML iteration IDs to GitLab iteration IDs
        self.milestone_id_map = {}  # Maps YAML milestone IDs to GitLab milestone IDs
        self.user_id_map = {}       # Maps YAML user IDs to GitLab user IDs
    
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
        
            logger.info("Validating YAML against schema…")
            try:
                jsonschema.validate(instance=data, schema=schema)
                logger.info("YAML validation successful!")
            except jsonschema.ValidationError as e:
                logger.error(f"YAML validation failed: {e}")
                sys.exit(1)
            
            # Process users first
            if 'users' in data:
                for user_data in data.get('users', []):
                    self.process_user(user_data)
        
            # Process top-level groups
            for group_data in data.get('groups', []):
                self.process_group(group_data, self.parent_group_id)
        
            logger.info("YAML processing completed successfully!")
        
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML file: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"An error occurred: {e}\n{traceback.format_exc()}")
            sys.exit(1)

    def process_user(self, user_data: Dict[str, Any]) -> Optional[str]:
        """
        Process user data and map to GitLab users.
        
        Args:
            user_data: Dictionary containing user definition
            
        Returns:
            The username of the user or None if not found
        """
        user_id = user_data.get('id')
        username = user_data.get('username')
        assert user_id is not None, "User ID is missing for user"
        assert username is not None, f"Username is missing for user ID '{user_id}'"
        assert username.startswith('@'), f"Username '{username}' is not a valid GitLab username"
        
        # Handle special case for @me
        if username == "@me":
            self.user_id_map[user_id] = self.current_user
            logger.info(f"Mapped user ID '{user_id}' to current user '{self.current_user}'")
            return self.current_user
        
        # Remove @ from username for GitLab API calls
        gitlab_username = username[1:]
        
        try:
            # Try to find the user in GitLab
            users_list = self.gl.users.list(username=gitlab_username)
            users = list(users_list)  # Convert RESTObjectList to a regular list
            if users and len(users) > 0:
                assert len(users) == 1, f"Multiple users found for username '{username}'"
                user = users[0]
                self.user_id_map[user_id] = user.username
                logger.info(f"Found GitLab user '{user.username}' for user ID '{user_id}'")
                return user.username
            else:
                logger.error(f"User '{username}' not found in GitLab. References to this user will be ignored.")
                return None
        except gitlab.GitlabError as e:
            logger.error(f"Error finding user '{username}': {e}")
            return None

    def process_group(self, group_data: Dict[str, Any], parent_id: Optional[int] = None) -> Optional[int]:
        """
        Process and create a group.
        
        Args:
            group_data: Dictionary containing group definition
            parent_id: ID of parent group (None for top-level groups)
            
        Returns:
            The ID of the created/found group
        """
        group_name = group_data.get('name')
        group_desc = group_data.get('description', '')
        assert group_name is not None, "Group name is missing"
        assert group_desc is not None, "Group description is missing"
        
        # Group path must be URL friendly
        group_path = group_name.lower().replace(' ', '-')
        
        try:
            # Check if group exists
            if parent_id:
                parent_group = self.gl.groups.get(parent_id)
                full_path = f"{parent_group.full_path}/{group_path}"
                try:
                    group = self.gl.groups.get(full_path)
                    logger.error(f"Group already exists: {full_path} (GitLab ID: {group.id})")
                    return None
                except gitlab.GitlabGetError:
                    # Create group
                    group = self.gl.groups.create({
                        'name': group_name,
                        'path': group_path,
                        'parent_id': parent_id,
                        'description': group_desc,
                        'visibility': 'private'  # Adjust as needed
                    })
                    logger.info(f"Created group: {full_path} (GitLab ID: {group.id})")
            else:
                try:
                    group = self.gl.groups.get(group_path)
                    logger.error(f"Top-level group already exists: {group_path} (GitLab ID: {group.id})")
                    return None
                except gitlab.GitlabGetError:
                    # Create top-level group
                    group = self.gl.groups.create({
                        'name': group_name,
                        'path': group_path,
                        'description': group_desc,
                        'visibility': 'private'  # Adjust as needed
                    })
                    logger.info(f"Created top-level group: {group_path} (GitLab ID: {group.id})")
            
            # Process members at group level
            for member_data in group_data.get('members', []):
                self.process_member(member_data, group)
                
            # Process labels at group level
            for label_data in group_data.get('labels', []):
                self.process_label(label_data, group)
            
            # Process iterations at group level (only available with GitLab Premium/Ultimate)
            for iteration_data in group_data.get('iterations', []):
                self.process_iteration(iteration_data, group)
                
            # Process milestones at group level
            for milestone_data in group_data.get('milestones', []):
                self.process_milestone(milestone_data, group)
            
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
    
    def process_label(self, label_data: Dict[str, Any], group_or_project: Any) -> Optional[int]:
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
        assert label_id is not None, "Label ID is missing"
        assert label_name is not None, "Label name is missing"
        assert label_color is not None, "Label color is missing"
        assert label_desc is not None, "Label description is missing"
        
        labels_manager = group_or_project.labels
        
        try:
            # Check if label exists
            try:
                # Using find instead of get as get may raise an error for multiple matches
                existing_labels = list(labels_manager.list(search=label_name))
                existing_label = next((l for l in existing_labels if l.name == label_name), None)

                if existing_label:
                    logger.error(f"Label already exists: '{label_name}'")
                    return None
            except (gitlab.GitlabGetError, StopIteration):
                existing_label = None
            
            # Create label if it doesn't exist
            if not existing_label:
                label = labels_manager.create({
                    'name': label_name,
                    'color': label_color,
                    'description': label_desc
                })
                logger.info(f"Created label: '{label_name}'")
                self.label_name_map[label_id] = label_name
                return label.id
                
        except gitlab.GitlabCreateError as e:
            logger.error(f"Error creating label '{label_name}': {e}")
            raise
    
    def process_iteration(self, iteration_data: Dict[str, Any], group: Any) -> Optional[int]:
        """
        Process and create an iteration.
        
        Args:
            iteration_data: Dictionary containing iteration definition
            group: GitLab group object
            
        Returns:
            The ID of the created/found iteration
        """

        iteration_id = iteration_data.get('id')
        iteration_title = iteration_data.get('title')
        iteration_desc = iteration_data.get('description', '')
        iteration_start_date = iteration_data.get('start_date')
        iteration_due_date = iteration_data.get('due_date')
        iteration_state = iteration_data.get('state', 'active')
        assert iteration_id is not None, "Iteration ID is missing"
        assert iteration_title is not None, "Iteration title is missing"
        assert iteration_desc is not None, "Iteration description is missing"
        assert iteration_start_date is not None, "Iteration start date is missing"
        assert iteration_due_date is not None, "Iteration due date is missing"
        assert iteration_state is not None, "Iteration state is missing"
        
        try:
            # Search for existing iteration by title
            existing_iterations = list(group.iterations.list(search=iteration_title))
            iteration = next((i for i in existing_iterations if i.title == iteration_title and i.group_id == group.id), None)
            
            if iteration:
                logger.error(f"Iteration with same name already exists: '{iteration_title}' (GitLab ID: {iteration.id})")
                return None
            
            # Create iteration using GraphQL API
            # Define the GraphQL mutation for creating an iteration
            create_iteration_mutation = """
            mutation createIteration($input: CreateIterationInput!) {
                createIteration(input: $input) {
                iteration {
                    id
                }
                errors
                }
            }
            """
            
            # Prepare variables for the GraphQL mutation
            variables = {
                "input": {
                    "groupPath": group.full_path,
                    "title": iteration_title,
                    "description": iteration_desc
                }
            }
            
            # Add dates if provided
            if iteration_start_date:
                variables["input"]["startDate"] = iteration_start_date
            if iteration_due_date:
                variables["input"]["dueDate"] = iteration_due_date

            # Set as closed if required
            if iteration_state == 'closed':
                variables["input"]["stateEvent"] = "close"
            
            # Execute the GraphQL mutation
            result = self.gq.execute(create_iteration_mutation, variables)
            
            if result and 'createIteration' in result and 'iteration' in result['createIteration']:
                iteration_data = result['createIteration']['iteration']
                iteration_id_gitlab = iteration_data['id']
                id = int(iteration_id_gitlab.split('/')[-1])
                logger.info(f"Created iteration: '{iteration_title}' (GitLab ID: {id})")
            else:
                errors = result.get('createIteration', {}).get('errors', [])
                logger.error(f"Error creating iteration '{iteration_title}' via GraphQL API: {errors}")
                return None
            
            self.iteration_id_map[iteration_id] = id
            
            return id
            
        except gitlab.GitlabListError as e:
            if "403" in str(e):
                logger.error(f"Error listing iterations (may be due to missing a premium/ultimate license): {e}")
                return None
            logger.error(f"Error listing iterations: {e}")
            raise
        except Exception as e:
            logger.error(f"Error processing iteration '{iteration_title}': {e}")
            return None
    
    def process_milestone(self, milestone_data: Dict[str, Any], group_or_project: Any) -> Optional[int]:
        """
        Process and create a milestone.
        
        Args:
            milestone_data: Dictionary containing milestone definition
            group_or_project: GitLab group or project object
            
        Returns:
            The ID of the created/found milestone
        """
        milestones_manager = group_or_project.milestones
        
        milestone_id = milestone_data.get('id')
        milestone_title = milestone_data.get('title')
        milestone_desc = milestone_data.get('description', '')
        milestone_start_date = milestone_data.get('start_date')
        milestone_due_date = milestone_data.get('due_date')
        milestone_state = milestone_data.get('state', 'active')
        assert milestone_id is not None, "Milestone ID is missing"
        assert milestone_title is not None, "Milestone title is missing"
        assert milestone_desc is not None, "Milestone description is missing"
        assert milestone_start_date is not None, "Milestone start date is missing"
        assert milestone_due_date is not None, "Milestone due date is missing"
        assert milestone_state is not None, "Milestone state is missing"
        
        try:
            # Search for existing milestone by title
            existing_milestones = list(milestones_manager.list(search=milestone_title))
            milestone = next((m for m in existing_milestones if m.title == milestone_title), None)
            
            if milestone:
                logger.error(f"Milestone with same name already exists: '{milestone_title}' (GitLab ID: {milestone.id})")
                return None

            # Create milestone
            milestone_data = {
                'title': milestone_title,
                'description': milestone_desc
            }
            
            # Add dates if provided
            if milestone_start_date:
                milestone_data['start_date'] = milestone_start_date
            if milestone_due_date:
                milestone_data['due_date'] = milestone_due_date
            
            milestone = milestones_manager.create(milestone_data)
            logger.info(f"Created milestone: '{milestone_title}' (GitLab ID: {milestone.id})")
            
            self.milestone_id_map[milestone_id] = milestone.id
            
            # Update milestone state if needed
            if milestone_state == 'closed' and milestone.state != 'closed':
                milestone.state_event = 'close'
                milestone.save()
                logger.info(f"Closed milestone: '{milestone_title}' (GitLab ID: {milestone.id})")
            
            return milestone.id
            
        except gitlab.GitlabCreateError as e:
            logger.error(f"Error creating milestone '{milestone_title}': {e}")
            raise
    
    def process_epic(self, epic_data: Dict[str, Any], group: Any) -> Optional[int]:
        """
        Process and create an epic.
        
        Args:
            epic_data: Dictionary containing epic definition
            group: GitLab group object
            
        Returns:
            The ID of the created/found epic
        """
        epic_id = epic_data.get('id')
        epic_title = epic_data.get('title')
        epic_desc = epic_data.get('description')
        epic_state = epic_data.get('state', 'opened')
        epic_labels = epic_data.get('label_ids', [])
        epic_epic_parent_id = epic_data.get('parent_epic_id', None)
        assert epic_id is not None, "Epic ID is missing"
        assert epic_title is not None, "Epic title is missing"
        assert epic_desc is not None, "Epic description is missing"
        assert epic_state is not None, "Epic state is missing"
        assert epic_labels is not None, "Epic labels are missing"
        assert epic_epic_parent_id is not None, "Epic parent epic ID is missing"
        
        try:
            # Search for existing epic by title
            existing_epics = list(group.epics.list(search=epic_title))
            epic = next((e for e in existing_epics if e.title == epic_title), None)
            if epic:
                logger.warning(f"Epic with same title already exists: '{epic_title}' (GitLab ID: {epic.id})")

            # Create epic
            epic = group.epics.create({
                'title': epic_title,
                'description': epic_desc,
                'state': epic_state
            })
            logger.info(f"Created epic: '{epic_title}' (GitLab ID: {epic.id})")
            self.epic_id_map[epic_id] = epic.id
            
            # Update epic state if needed
            if epic_state == 'closed' and epic.state != 'closed':
                epic.state_event = 'close'
                epic.save()
                logger.info(f"Closed epic: '{epic_title}' (GitLab ID: {epic.id})")
            
            # Add labels to epic
            for label_id in epic_labels:
                if label_id in self.label_name_map:
                    try:
                        epic.labels.append(self.label_name_map[label_id])
                        epic.save()
                        logger.info(f"Added label '{self.label_name_map[label_id]}' to epic '{epic.title}'")
                    except Exception as e:
                        logger.error(f"Error adding label '{self.label_name_map[label_id]}' to epic: {e}")
                else:
                    logger.error(f"Label id='{label_id}' not found in label map")
            
            # Set parent epic if provided
            if epic_epic_parent_id:
                parent_epic = self.epic_id_map.get(epic_epic_parent_id)
                if parent_epic:
                    epic.parent_id = parent_epic
                    epic.save()
                    logger.info(f"Set parent epic (GitLab ID: {parent_epic}) for epic '{epic.title}'")
                else:
                    logger.error(f"Parent epic id='{epic_epic_parent_id}' not found in epic map")

            return epic.id

        except gitlab.GitlabListError as e:
            if "403" in str(e):
                logger.error(f"Error listing epics (may be due to missing a premium/ultimate license): {e}")
                return None
            logger.error(f"Error listing epics: {e}")
            raise
        except gitlab.GitlabCreateError as e:
            logger.error(f"Error creating epic '{epic_title}': {e}")
            raise

    def process_project(self, project_data: Dict[str, Any], group: Any) -> Optional[int]:
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
        assert project_name is not None, "Project name is missing"
        assert project_desc is not None, "Project description is missing"
        
        try:
            # Check if project exists in group
            existing_projects = list(group.projects.list(search=project_name))
            existing_project = next((p for p in existing_projects if p.name == project_name), None)
            
            if existing_project:
                logger.error(f"Project with same name already exists: '{project_name}' (GitLab ID: {existing_project.id})")
                return None

            # Create project
            project = self.gl.projects.create({
                'name': project_name,
                'namespace_id': group.id,
                'description': project_desc,
                'visibility': 'private'  # Adjust as needed
            })
            logger.info(f"Created project: '{project_name}' (GitLab ID: {project.id})")
            
            # Give GitLab some time to initialize the project
            time.sleep(1)
            
            # Reload project to ensure all attributes are available
            project = self.gl.projects.get(project.id)
            
            # Process members in project
            for member_data in project_data.get('members', []):
                self.process_member(member_data, project)
                
            # Process milestones in project
            for milestone_data in project_data.get('milestones', []):
                self.process_milestone(milestone_data, project)
            
            # Process issues in project
            for issue_data in project_data.get('issues', []):
                self.process_issue(issue_data, project)
            
            return project.id
                
        except gitlab.GitlabCreateError as e:
            logger.error(f"Error creating project '{project_name}': {e}")
            raise
    
    def process_member(self, member_data: Dict[str, Any], group_or_project: Any) -> None:
        """
        Process and add a member to a group or project.
        
        Args:
            member_data: Dictionary containing member definition
            group_or_project: GitLab group or project object
        """
        user_id = member_data.get('user_id')
        role = member_data.get('role')
        assert user_id is not None
        assert role is not None
        
        # Map role to GitLab access level
        access_level_map = {
            'guest': 10,
            'planner': 15,
            'reporter': 20,
            'developer': 30,
            'maintainer': 40,
            'owner': 50
        }
        
        access_level = access_level_map.get(role)
        assert access_level is not None, f"Invalid role '{role}'"
        
        if user_id in self.user_id_map:
            username = self.user_id_map[user_id]
            
            # TBD what do we do with this?
            ## Skip if trying to add current user as they already have access
            #if username == self.current_user:
            #    logger.info(f"Skipping adding current user '{username}' as member")
            #    return
            
            try:
                # Try to find the user in GitLab
                users_list = self.gl.users.list(username=username)
                users = list(users_list)  # Convert RESTObjectList to a regular list
                assert users is not None, f"User '{username}' not found in GitLab"
                assert len(users) == 1, f"Multiple users found for username '{username}'"
                user = users[0]
                try:
                    # Check if user is already a member
                    existing_members = list(group_or_project.members.list())
                    is_member = any(m.username == username for m in existing_members)
                        
                    if not is_member:
                        # Add user as member
                        group_or_project.members.create({
                            'user_id': user.id,
                            'access_level': access_level
                        })
                        logger.info(f"Added user '{username}' as {role} to {group_or_project.name}")
                    else:
                        logger.info(f"User '{username}' is already a member of {group_or_project.name}")
                except gitlab.GitlabCreateError as e:
                    logger.error(f"Error adding member '{username}': {e}")
            except gitlab.GitlabError as e:
                logger.error(f"Error finding user '{username}': {e}")
        else:
            logger.error(f"User ID '{user_id}' not found in user map")
    
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
        issue_desc = issue_data.get('description')
        issue_state = issue_data.get('state', 'opened')
        issue_labels = issue_data.get('label_ids', [])
        issue_parent_epic_id = issue_data.get('parent_epic_id', None)
        issue_milestone_id = issue_data.get('milestone_id', None)
        issue_iteration_id = issue_data.get('iteration_id', None)
        issue_weight = issue_data.get('weight', None)
        issue_assignee_ids = issue_data.get('assignee_ids', [])
        assert issue_id is not None, "Issue ID is missing"
        assert issue_title is not None, "Issue title is missing"
        assert issue_desc is not None, "Issue description is missing"
        assert issue_state is not None, "Issue state is missing"
        assert issue_labels is not None, "Issue labels are missing"
        assert issue_parent_epic_id is not None, "Issue parent epic ID is missing"
        assert issue_milestone_id is not None, "Issue milestone ID is missing"
        assert issue_iteration_id is not None, "Issue iteration ID is missing"
        assert issue_weight is not None, "Issue weight is missing"
        assert issue_assignee_ids is not None, "Issue assignee IDs are missing"

        try:
            # Search for existing issue by title
            existing_issues = list(project.issues.list(search=issue_title))
            issue = next((i for i in existing_issues if i.title == issue_title), None)
            
            if issue:
                logger.info(f"Issue already exists with same title: '{issue_title}' (GitLab ID: {issue.id})")

            # Set iteration if provided
            # There is currently no API to set the iteration of an issue
            # See https://gitlab.com/gitlab-org/gitlab/-/issues/395790
            # So we are obliged to use the "/iteration *iteration:<iteration ID>" quick action for the time being
            if issue_iteration_id:
                iteration_id = self.iteration_id_map.get(issue_iteration_id)
                if iteration_id:
                    issue_desc = f"{issue_desc}\n/iteration *iteration:{iteration_id}"
                    logger.info(f"Set iteration (GitLab ID: {iteration_id}) will be set for issue '{issue_title}'")
                else:
                    logger.error(f"Iteration id='{issue_iteration_id}' not found in iteration map")

            # Create issue
            issue = project.issues.create({
                'title': issue_title,
                'description': issue_desc
            })
            logger.info(f"Created issue: '{issue_title}' (GitLab ID: {issue.id})")
            self.issue_id_map[issue_id] = issue.id

            # Set weight if provided
            if issue_weight is not None:
                issue.weight = issue_weight
                issue.save()
                logger.info(f"Set weight ({issue_weight}) for issue '{issue.title}'")

            # Update issue state if needed
            if issue_state == 'closed' and issue.state != 'closed':
                issue.state_event = 'close'
                issue.save()
                logger.info(f"Closed issue: '{issue_title}' (GitLab ID: {issue.id})")
            
            # Add labels to issue
            for label_id in issue_labels:
                if label_id in self.label_name_map:
                    try:
                        issue.labels.append(self.label_name_map[label_id])
                        issue.save()
                        logger.info(f"Added label '{self.label_name_map[label_id]}' to issue '{issue.title}'")
                    except Exception as e:
                        logger.error(f"Error adding label '{self.label_name_map[label_id]}' to issue: {e}")
                else:
                    logger.error(f"Label id='{label_id}' not found in label map")

            # Set parent epic if provided
            if issue_parent_epic_id:
                parent_epic = self.epic_id_map.get(issue_parent_epic_id)
                if parent_epic:
                    issue.epic_id = parent_epic
                    issue.save()
                    logger.info(f"Set parent epic (GitLab ID: {parent_epic}) for issue '{issue.title}'")
                else:
                    logger.error(f"Parent epic id='{issue_parent_epic_id}' not found in epic map")
            
            # Set milestone if provided
            if issue_milestone_id:
                milestone_id = self.milestone_id_map.get(issue_milestone_id)
                if milestone_id:
                    issue.milestone_id = milestone_id
                    issue.save()
                    logger.info(f"Set milestone (GitLab ID: {milestone_id}) for issue '{issue.title}'")
                else:
                    logger.error(f"Milestone id='{issue_milestone_id}' not found in milestone map")
            
            # Set assignees if provided
            if issue_assignee_ids:
                assignee_ids = []
                for assignee_id in issue_assignee_ids:
                    if assignee_id in self.user_id_map:
                        username = self.user_id_map[assignee_id]
                        try:
                            # Find user by username
                            users_list = self.gl.users.list(username=username)
                            users = list(users_list)  # Convert RESTObjectList to a regular list
                            assert users is not None, f"User '{username}' not found in GitLab"
                            assert len(users) == 1, f"Multiple users found for username '{username}'"
                            user = users[0]
                            assignee_ids.append(user.id)
                            logger.info(f"Will assign user '{username}' to issue '{issue_title}'")
                        except gitlab.GitlabError as e:
                            logger.error(f"Error finding user '{username}': {e}")
                            logger.warning(f"User '{username}' not found in GitLab. Cannot assign to issue.")
                    else:
                        logger.error(f"User ID '{assignee_id}' not found in user map")
                
                if assignee_ids:
                    issue.assignee_ids = assignee_ids
                    issue.save()
                    logger.info(f"Assigned users to issue '{issue_title}'")
                        
            return issue.id

        except gitlab.GitlabCreateError as e:
            logger.error(f"Error creating issue '{issue_title}': {e}")
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
    
    args = parser.parse_args()
    
    creator = GitLabInjector(gitlab_url=args.url, private_token=args.token, parent_group_path=args.group)
    creator.process_yaml(args.config)

if __name__ == '__main__':
    main()