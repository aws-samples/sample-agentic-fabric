if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

import json
from typing import Any
from strands import Agent, tool, models
from strands_tools import file_write, http_request, shell
import os
from tools_config import load_config_from_dynamodb, create_tool_desc
import boto3
from botocore.config import Config

os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

def get_tool_fabricator_prompt():
    """System prompt for the Tool Fabricator agent"""
    TOOL_FABRICATOR_PROMPT = """
    <role>
    You are the Tool Fabricator Agent. Your sole responsibility is to generate custom Python tool functions using the @tool decorator from the Strands SDK and persist them to storage.
    </role>

    <mandatory_tool_structure>
    Every tool you create MUST follow this pattern:

    from strands import tool

    @tool
    def tool_name(param: type) -> return_type:
        \"""Clear description of what the tool does.
        
        Args:
            param: Description of parameter
            
        Returns:
            Description of return value
        \"""
        # Implementation here
        return result

    <rules>
    - Tool function MUST use @tool decorator
    - MUST have clear docstring with Args and Returns sections
    - MUST have type hints
    - NO tests, NO example usage code
    - Keep implementation simple and focused
    - Use standard library when possible
    - NO external API calls unless absolutely necessary
    </rules>
    </mandatory_tool_structure>

    <critical_workflow>
    For EVERY tool request, you MUST execute ALL FOUR steps using your available tools:
    
    STEP 1: Design the tool mentally (determine name, parameters, return type, implementation)
    
    STEP 2: Call file_write tool to save it to /tmp/[tool_name].py
    
    STEP 3: Call upload_tool_to_s3 tool with the file path
    
    STEP 4: Call store_tool_config_dynamo tool with:
       - file_name: "/tmp/[tool_name].py"
       - tool_id: the tool function name (e.g., "calculate_percentage")
       - tool_schema: OpenAPI-compliant JSON schema like:
         {
           "type": "object",
           "properties": {
             "param_name": {
               "type": "string",
               "description": "Parameter description"
             }
           },
           "required": ["param_name"]
         }
       - tool_description: brief one-sentence description
    
    CRITICAL: The tool is NOT registered and CANNOT be used until store_tool_config_dynamo is called.
    If you skip this step, the tool will be lost and unusable.
    
    YOU MUST ACTUALLY CALL ALL THREE TOOLS IN SEQUENCE. Do not just describe what you would do.
    After calling each tool, verify it succeeded before moving to the next step.
    </critical_workflow>

    <example_execution>
    User request: "Create a tool that validates email addresses"
    
    Your actions (YOU MUST DO ALL OF THESE IN ORDER):
    
    Step 1 - Design the tool:
    - Tool name: validate_email
    - Parameters: email (string)
    - Returns: boolean
    
    Step 2 - Generate the complete tool code as a string
    
    Step 3 - CALL file_write tool (REQUIRED):
    file_write(
        path="/tmp/validate_email.py",
        content='''from strands import tool

    @tool
    def validate_email(email: str) -> bool:
        \"""Validate if a string is a valid email address.
        
        Args:
            email: Email address to validate
            
        Returns:
            True if valid email, False otherwise
        \"""
        import re
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))
    '''
        )
        ✓ Step 3 complete - file written
        
        Step 4 - CALL upload_tool_to_s3 tool (REQUIRED):
        upload_tool_to_s3(file_path="/tmp/validate_email.py")
        ✓ Step 4 complete - file uploaded to S3
        
        Step 5 - CALL store_tool_config_dynamo tool (REQUIRED - MOST CRITICAL STEP):
        store_tool_config_dynamo(
            file_name="/tmp/validate_email.py",
            tool_id="validate_email",
            tool_schema={
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "description": "Email address to validate"
                    }
                },
                "required": ["email"]
            },
            tool_description="Validates if a string is a valid email address"
        )
        ✓ Step 5 complete - tool registered in DynamoDB
        
        Step 6 - Verify all three tools were called:
        ✓ file_write - YES
        ✓ upload_tool_to_s3 - YES  
        ✓ store_tool_config_dynamo - YES
        
        ✅ TOOL CREATION COMPLETE - Tool is now discoverable and usable by other agents
        
        CRITICAL: You must call ALL THREE tools (file_write, upload_tool_to_s3, store_tool_config_dynamo) for every tool creation request.
        If you skip store_tool_config_dynamo, the tool will be created but NOT registered, making it invisible to the system.
        </example_execution>

        <tool_code_example>
        from strands import tool

        @tool
        def calculate_percentage(value: float, total: float) -> float:
            \"""Calculate the percentage of a value relative to a total.
            
            Args:
                value: The value to calculate percentage for
                total: The total value to compare against
                
            Returns:
                The percentage as a float
            \"""
            if total == 0:
                return 0.0
            return (value / total) * 100.0
        </tool_code_example>

        <reminder>
        ⚠️ MANDATORY: You have THREE tools that MUST ALL be called for EVERY tool creation ⚠️
        
        1. file_write - Save the tool code to /tmp/[tool_name].py
        2. upload_tool_to_s3 - Upload the file to S3 storage
        3. store_tool_config_dynamo - Register the tool in DynamoDB
        
        ❌ FAILURE MODE: If you only call file_write and upload_tool_to_s3 but skip store_tool_config_dynamo,
        the tool will NOT be registered in DynamoDB and CANNOT be discovered or used by other agents.
        
        ✅ SUCCESS CRITERIA: The tool creation is ONLY complete when ALL THREE tools have been called successfully.
        
        Do not just return text describing what you did - you must ACTUALLY CALL all three tools.
        After each tool call, verify it succeeded before proceeding to the next step.
        
        VERIFICATION CHECKLIST:
        □ Called file_write with tool code
        □ Called upload_tool_to_s3 with file path
        □ Called store_tool_config_dynamo with tool_id, schema, and description
        
        Only after all three checkboxes are complete is the tool ready for use.
        </reminder>
    """
    return TOOL_FABRICATOR_PROMPT

def get_agent_fabricator_prompt():
    """System prompt for the Agent Fabricator agent"""
    tool_configs = load_config_from_dynamodb()
    worker_tools_list = '\n'.join(create_tool_desc(tool_configs))

    AGENT_FABRICATOR_PROMPT = f"""
    <role>
    You are the Fabricator Agent in a multi-agent system. Your sole responsibility is to generate Python code for new Strands agents that execute tasks defined by a Supervisor Agent. You do not execute tasks—you create the agents that will.
    </role>

    <architecture>
    Supervisor → requests new agent capability
    ↓
    Fabricator (YOU) → generates agent code
    ↓
    Evaluator → validates compliance before deployment
    </architecture>

    <mandatory_code_structure>
    Every agent you create MUST follow the code template.
    the agent will be called by the supervisor by using the "handler" function.
    if you call the main entry point anything other than "handler" it will not execute.

    <code_template>
    from strands import Agent, models

    def handler(input_param):
        \"""Clear docstring: purpose, inputs, outputs, constraints\"""
        bedrock_model = models.BedrockModel(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            region_name="us-west-2"
        )
        agent = Agent(bedrock_model, tools=[...])
        result = agent("task prompt")
        return result
    </code_template>

    <non_negotiable_rules>
    - Function MUST be named `handler`
    - MUST use `models.BedrockModel` from strands package
    - MUST be a single, importable Python file
    - MUST include module-level docstring
    - NO tests, NO UI, NO user interaction code
    </non_negotiable_rules>
    </mandatory_code_structure>

    <tool_selection_hierarchy>
    Follow this priority when choosing tools:

    <priority_1_strands_builtin_tools>
    Use these first if they meet the requirement:
    - file_read, file_write, editor - File operations
    - shell - OS commands
    - http_request - API calls
    - python_repl - Python code execution
    - calculator - Math operations
    - use_aws - AWS services
    - retrieve - Bedrock Knowledge Base
    - memory, mem0_memory, environment - Persistent storage
    - journal, speak - Logging/output
    - generate_image, image_reader - Image operations
    - think - Complex reasoning
    - current_time, sleep, stop - Utilities
    - swarm, workflow, batch - Orchestration
    - use_llm - customized system prompts for specialized tasks
    </priority_1_strands_builtin_tools>

    <priority_2_worker_tools>
    If no Strands tool fits, check Worker Tools List:
    {worker_tools_list}

    Read the function code and write it into the agent as shown in examples below.
    </priority_2_worker_tools>

    <priority_3_custom_tools>
    Only create custom tools if Priorities 1 and 2 cannot satisfy the requirement.

    IMPORTANT: DO NOT write custom tool code yourself. Instead, use the create_custom_tool function:
    1. Call create_custom_tool with a detailed description of what the tool should do
    2. The Tool Fabricator will generate the tool code for you
    3. Extract the tool function code from the response
    4. Include the tool code directly in your agent file

    Example usage:
    tool_code = create_custom_tool("Create a tool that validates email addresses and returns True if valid, False otherwise")
    # Then include the returned tool code in your agent
    </priority_3_custom_tools>
    </tool_selection_hierarchy>

    <agent_with_custom_tool_pattern>
    When you need a custom tool, request it from the Tool Fabricator and include the generated code:

    <workflow>
    1. Identify that you need a custom tool
    2. Call create_custom_tool("description of what the tool should do")
    3. Parse the tool code from the Tool Fabricator's response
    4. Include the tool code in your agent file
    5. Use the tool in your agent
    </workflow>

    <example>
    from strands import Agent, tool, models

    # Tool code generated by Tool Fabricator (included directly in agent file)
    @tool
    def validate_email(email: str) -> bool:
        \"""Validate if a string is a valid email address.
        
        Args:
            email: Email address to validate
            
        Returns:
            True if valid email, False otherwise
        \"""
        import re
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{{2,}}$'
        return bool(re.match(pattern, email))

    def handler(email_address: str) -> str:
        \"""Agent that validates email addresses using custom tool\"""
        bedrock_model = models.BedrockModel(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            region_name="us-west-2"
        )
        agent = Agent(bedrock_model, tools=[validate_email])
        result = agent(f"Is this a valid email: {{email_address}}?")
        return result
    </example>
    </agent_with_custom_tool_pattern>

    <governance_compliance>
    Your generated agents MUST:
    - Use /tmp/ for all file writes before S3 upload
    - Be deterministic (no random behavior unless explicitly required)
    - Include meaningful logging via journal or speak tools

    Your generated agents MUST NOT:
    - Use unbounded recursion
    - Use unrestricted shell execution
    - Expose credentials in code
    - Use self-modifying code
    </governance_compliance>

    <autonomous_operation>
    - DO NOT ask clarifying questions
    - DO make reasonable assumptions and document them
    - DO infer missing details logically
    - DO include assumptions in your AGENT DESIGN SUMMARY
    </autonomous_operation>

    <output_format>
    Provide your response in EXACTLY this format:

    <section name="agent_design_summary">
    Purpose: [One sentence describing what the agent does]
    Input: [Type and description of handler parameter]
    Output: [Type and description of return value]
    Tools Required: [List of tools from priority order]
    Assumptions Made: [Any inferences you made from the request]
    Risk Rating: [low/medium/high based on tool permissions needed]
    Policy Considerations: [Any governance flags for Evaluator]
    </section>

    <section name="filename">
    agent_name.py
    </section>

    <section name="code">
    Provide ONLY the Python code. NO backticks, NO markdown formatting.
    the main entry function must be 'def handler'
    </section>

    <section name="metadata">
    {{
    "agent_name": "descriptive_agent_name",
    "purpose": "Brief description",
    "tools_used": ["tool1", "tool2"],
    "custom_tools_defined": ["custom_tool_name"],
    "requires_external_permissions": false,
    "risk_rating": "low",
    "s3_path": "s3://agents/agent_name.py",
    "dynamodb_record": {{
        "agent_id": "agent_name",
        "handler_function": "handler",
        "created_by": "fabricator",
        "version": "1.0"
        }}
    }}
    </section>
    </output_format>

    <examples>
    <example_1 name="Agent Using Strands Built-in Tool">
    from strands import Agent, models
    from strands_tools import calculator

    def handler(x: int) -> str:
        \"""Calculate the square root of a number.
        Args:
            x: Integer to calculate square root of

        Returns:
            String containing the result
        \"""
        bedrock_model = models.BedrockModel(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            region_name="us-west-2"
        )
        agent = Agent(bedrock_model, tools=[calculator])
        result = agent(f"What is the square root of {{x}}?")
        return result
    </example_1>

    <example_2 name="Agent Using Custom Tool">
    from strands import Agent, tool, models

    @tool
    def word_count(text: str) -> int:
        \"""Count the number of words in provided text.
        Args:
            text: String to count words in

        Returns:
            Integer count of words
        \"""
        return len(text.split())

    def handler(text: str) -> str:
        \"""Count words in the provided text string.
        Args:
            text: Input string to analyze

        Returns:
            Agent response with word count
        \"""
        bedrock_model = models.BedrockModel(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            region_name="us-west-2"
        )
        agent = Agent(bedrock_model, tools=[word_count])
        result = agent(f"How many words are in this text: '{{text}}'")
        return result
    </example_2>
    </examples>

    <checklist>
    Before submitting, verify:
    - Agent function is named handler
    - Uses models.BedrockModel from strands import
    - Follows tool priority hierarchy
    - Includes complete docstrings
    - No test code included
    - Single file, importable as module
    - Uses /tmp/ for file operations
    - All 4 output sections present
    - Code has NO markdown backticks
    - Metadata JSON is valid
    - Store agent file in s3
    - Register agent config to agent dynamoDB
    </checklist>

    <reminder>
    You are generating production code. Prioritize clarity, safety, and compliance over cleverness.
    Always make sure the main entry point for the agent is called handler.
    Import statement: from strands import Agent, tool, models

    IMPORTANT: If you need a custom tool, DO NOT create it yourself. Instead, call the create_custom_tool function with a description of what the tool should do. The Tool Fabricator will generate the tool code for you, and you can then include it in your agent code.
    </reminder>
    """
    return AGENT_FABRICATOR_PROMPT

def publish_fabrication_event(workflow_id: str, event_type: str, agent_id: str = None, error: str = None):
    """Publish fabrication event to EventBridge
    
    Args:
        workflow_id: The orchestration ID for the fabrication request
        event_type: The type of event ('agent.fabricated' or 'agent.fabrication.failed')
        agent_id: The ID of the created agent (optional, for success events)
        error: Error message (optional, for failure events)
    
    Returns:
        The response from EventBridge put_events call
    """
    client = boto3.client('events')
    COMPLETION_BUS_NAME = os.environ.get('COMPLETION_BUS_NAME')
    
    event_detail = {
        'workflow_id': workflow_id,
    }
    
    if agent_id:
        event_detail['agent_use_id'] = agent_id
    
    if error:
        event_detail['error'] = error
    else:
        event_detail['data'] = 'Agent successfully created'
    
    event = {
        'Source': event_type,
        'DetailType': event_type,
        'EventBusName': COMPLETION_BUS_NAME,
        'Detail': json.dumps(event_detail)
    }
    
    response = client.put_events(Entries=[event])
    print(f"Published {event_type} event: {response}")
    return response

def upload_to_s3(file_path, folder):
    """Upload a file to S3"""
    s3 = boto3.client('s3')
    bucket_name = os.environ.get("AGENT_BUCKET_NAME", None)
    if bucket_name is None:
        raise ValueError("AGENT_BUCKET_NAME environment variable is not set")
    print(f"storing {file_path}")
    filename = file_path.split("/")[-1]
    s3.upload_file(file_path, bucket_name, f"{folder}/{filename}")

@tool
def upload_agent_to_s3(file_path):
    """Upload a agent file to S3"""
    upload_to_s3(file_path, "agents")

@tool
def upload_tool_to_s3(file_path):
    """Upload a tool file to S3"""
    upload_to_s3(file_path, "tools")

@tool
def get_worker_tool(tool_name: str) -> str:
    """Get tool code from s3
    
    Args:
        tool_name: Name of the tool file (e.g., 'my_tool.py')
        
    Returns:
        str: The tool code content from S3
        
    Raises:
        ValueError: If AGENT_BUCKET_NAME environment variable is not set
    """
    s3 = boto3.client('s3')
    bucket_name = os.environ.get("AGENT_BUCKET_NAME", None)
    
    if bucket_name is None:
        raise ValueError("AGENT_BUCKET_NAME environment variable is not set")
    
    # Construct the S3 key for the tool in the tools/ folder
    s3_key = f"tools/{tool_name}"
    
    print(f"Retrieving tool from s3://{bucket_name}/{s3_key}")
    
    try:
        response = s3.get_object(Bucket=bucket_name, Key=s3_key)
        tool_code = response['Body'].read().decode('utf-8')
        print(f"Successfully retrieved tool: {tool_name}")
        return tool_code
    except Exception as e:
        print(f"Error retrieving tool {tool_name}: {str(e)}")
        raise

@tool
def store_agent_config_dynamo(file_name: str, agent_id: str, llm_tool_schema: Any, agent_description: str):
    """Store agent configuration in DynamoDB.
    
    Requirements:
    - AGENT_CONFIG_TABLE_NAME environment variable must be set with the DynamoDB table name
    - DynamoDB table must use 'agentId' as the primary key
    
    Args:
        file_name (str): The filename where the agent implementation is stored
        agent_id (str): Unique identifier for the agent (used as primary key in DynamoDB)
        llm_tool_schema (Any): OpenAPI schema structure defining the agents parameters
                               Must follow OpenAPI format with properties, required fields, and types
                               Example: {
                                 "properties": {
                                   "param_name": {
                                     "description": "Parameter description",
                                     "type": "string"
                                   }
                                 },
                                 "required": ["param_name"],
                                 "type": "object"
                               }
        agent_description (str): Human-readable description of what the agent does
        
    Returns:
        bool: True if configuration was successfully stored
        
    Raises:
        ValueError: If AGENT_CONFIG_TABLE_NAME environment variable is not set
    """
    try:
        print(f"[store_agent_config_dynamo] Starting - agent_id: {agent_id}, file_name: {file_name}")
        
        dynamodb = boto3.resource('dynamodb')
        table_name = os.environ.get("AGENT_CONFIG_TABLE", None)
        
        print(f"[store_agent_config_dynamo] AGENT_CONFIG_TABLE env var: {table_name}")
        
        if table_name is None:
            error_msg = "AGENT_CONFIG_TABLE environment variable is not set"
            print(f"[store_agent_config_dynamo] ERROR: {error_msg}")
            raise ValueError(error_msg)

        # if llm_tool_schema is str then json loads it
        if isinstance(llm_tool_schema, str):
            print(f"[store_agent_config_dynamo] Converting llm_tool_schema from string to dict")
            llm_tool_schema = json.loads(llm_tool_schema)
        
        print(f"[store_agent_config_dynamo] llm_tool_schema type: {type(llm_tool_schema)}")
        print(f"[store_agent_config_dynamo] llm_tool_schema content: {json.dumps(llm_tool_schema, indent=2)}")

        table = dynamodb.Table(table_name)
        
        item = {
            'agentId': agent_id,
            'config': {
                "name": agent_id,
                "filename": file_name.split('/')[-1],
                "schema": llm_tool_schema,
                "version": '1',
                "description": agent_description,
                "action": {
                    "type": "sqs",
                    "target": os.environ.get("WORKER_QUEUE_URL", "MISSING")
                },
            },
            'state': 'inactive',
            'categories': ['worker']
        }
        
        print(f"[store_agent_config_dynamo] Attempting to put_item: {json.dumps(item, indent=2, default=str)}")
        
        response = table.put_item(Item=item)
        
        print(f"[store_agent_config_dynamo] SUCCESS - DynamoDB response: {response}")
        return True
        
    except Exception as e:
        print(f"[store_agent_config_dynamo] EXCEPTION: {type(e).__name__}: {str(e)}")
        import traceback
        print(f"[store_agent_config_dynamo] TRACEBACK: {traceback.format_exc()}")
        raise

@tool
def store_tool_config_dynamo(file_name: str, tool_id: str, tool_schema: Any, tool_description: str):
    """Store tool configuration in DynamoDB.
    
    Requirements:
    - TOOL_CONFIG_TABLE_NAME environment variable must be set with the DynamoDB table name
    - DynamoDB table must use 'toolId' as the primary key
    
    Args:
        file_name (str): The filename where the tool implementation is stored
        tool_id (str): Unique identifier for the tool (used as primary key in DynamoDB)
        tool_schema (Any): OpenAPI schema structure defining the tools parameters
                               Must follow OpenAPI format with properties, required fields, and types
                               Example: {
                                 "properties": {
                                   "param_name": {
                                     "description": "Parameter description",
                                     "type": "string"
                                   }
                                 },
                                 "required": ["param_name"],
                                 "type": "object"
                               }
        tool_description (str): Human-readable description of what the tool does
        
    Returns:
        bool: True if configuration was successfully stored
        
    Raises:
        ValueError: If TOOL_CONFIG_TABLE_NAME environment variable is not set
    """
    try:
        print(f"[store_tool_config_dynamo] Starting - tool_id: {tool_id}, file_name: {file_name}")
        
        dynamodb = boto3.resource('dynamodb')
        table_name = os.environ.get("TOOL_CONFIG_TABLE", None)
        
        print(f"[store_tool_config_dynamo] TOOL_CONFIG_TABLE env var: {table_name}")
        
        if table_name is None:
            error_msg = "TOOL_CONFIG_TABLE environment variable is not set"
            print(f"[store_tool_config_dynamo] ERROR: {error_msg}")
            raise ValueError(error_msg)

        # if tool_schema is str then json loads it
        if isinstance(tool_schema, str):
            print(f"[store_tool_config_dynamo] Converting tool_schema from string to dict")
            tool_schema = json.loads(tool_schema)
        
        print(f"[store_tool_config_dynamo] tool_schema type: {type(tool_schema)}")
        print(f"[store_tool_config_dynamo] tool_schema content: {json.dumps(tool_schema, indent=2)}")

        table = dynamodb.Table(table_name)
        
        item = {
            'toolId': tool_id,
            'config': {
                "name": tool_id,
                "filename": file_name.split('/')[-1],
                "schema": tool_schema,
                "version": '1',
                "description": tool_description,
            },
            'state': 'active'
        }
        
        print(f"[store_tool_config_dynamo] Attempting to put_item: {json.dumps(item, indent=2, default=str)}")
        
        response = table.put_item(Item=item)
        
        print(f"[store_tool_config_dynamo] SUCCESS - DynamoDB response: {response}")
        return True
        
    except Exception as e:
        print(f"[store_tool_config_dynamo] EXCEPTION: {type(e).__name__}: {str(e)}")
        import traceback
        print(f"[store_tool_config_dynamo] TRACEBACK: {traceback.format_exc()}")
        raise

def create_tool_fabricator():
    """Create and return the Tool Fabricator agent"""
    bedrock_model = models.BedrockModel(
        model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        max_tokens=4096,
        region_name="us-west-2",
    )
    
    tool_fabricator = Agent(
        bedrock_model,
        tools=[file_write, http_request, shell, upload_tool_to_s3, store_tool_config_dynamo],
        system_prompt=get_tool_fabricator_prompt()
    )
    
    return tool_fabricator

# Tool for Agent Fabricator to request custom tool creation
@tool
def create_custom_tool(tool_description: str) -> str:
    """Request the Tool Fabricator to create a custom tool.
    
    Args:
        tool_description: Detailed description of what the tool should do
        
    Returns:
        The generated tool code as a string
    """
    print(f"Agent Fabricator requesting custom tool: {tool_description}")
    
    # Create the Tool Fabricator agent
    tool_fabricator = create_tool_fabricator()

    # Call the Tool Fabricator agent
    result = tool_fabricator(f"Create a custom tool with the following requirements: {tool_description}")
    
    print(f"Tool Fabricator response: {result}")
    return result

def create_agent_fabricator(complete_task):
    """Create and return the Agent Fabricator agent"""
    bedrock_model = models.BedrockModel(
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            max_tokens=8192,
            region_name="us-west-2",
            boto_client_config=Config(read_timeout=3600),
    )
    
    agent_fabricator = Agent(
        bedrock_model,
        tools=[file_write, http_request, shell, get_worker_tool, create_custom_tool,
            upload_agent_to_s3, store_agent_config_dynamo, complete_task],
        system_prompt=get_agent_fabricator_prompt()
    )
    
    return agent_fabricator

def process_event(event, context, request_type=None):
    # Get values with defaults for direct requests
    workflow_id = event.get("workflow_id", "0")
    agent_use_id = event.get("agent_use_id", "unknown")
    request = event.get("agent_input", {})
    agent_name = event.get('node', 'fabricator')

    TASK = request.get("taskDetails", None)
    
    if TASK is None:
        print(f"Error: No taskDetails found in event: {event}")
        raise ValueError("taskDetails is required in agent_input")

    try:
        # since this needs variable injection, keep within handler method scope.
        @tool
        def complete_task():
            """Finally, call this to indicate the task has been completed"""
            client = boto3.client('events')
            COMPLETION_BUS_NAME = os.environ.get('COMPLETION_BUS_NAME')
            
            # Publish fabrication success event
            publish_fabrication_event(
                workflow_id=workflow_id,
                event_type='agent.fabricated',
                agent_id=agent_use_id
            )
            
            # Check if this is a direct request (workflow_id == '0') or part of an orchestration
            if workflow_id == '0':
                # Direct request from UI - send agent.fabricated event
                completion_event = {
                    'Source': 'agent.fabricated',
                    'DetailType': 'agent.fabricated',
                    'EventBusName': COMPLETION_BUS_NAME,
                    'Detail': json.dumps({
                        'workflow_id': workflow_id,
                        'data': 'Capability has been created',
                        'agent_use_id': agent_use_id,
                        'node': agent_name
                    })
                }
            else:
                # Part of orchestration - send task.completion event
                completion_event = {
                    'Source': 'task.completion',
                    'DetailType': 'task.completion',
                    'EventBusName': COMPLETION_BUS_NAME,
                    'Detail': json.dumps({
                        'workflow_id': workflow_id,
                        'data': 'Capability has been created, try to invoke it again.',
                        'agent_use_id': agent_use_id,
                        'node': agent_name
                    })
                }

            print("Completed")

            response = client.put_events(
                Entries=[
                    completion_event
                ]
            )
            print(f"event posted: {response}")
            return f"event posted: {completion_event}"

        if request_type == "tool-creation":
            # Create and invoke the Tool Fabricator
            tool_fabricator = create_tool_fabricator()
            tool_fabricator(TASK)
        else:
            # Create the Agent Fabricator with access to create_custom_tool
            agent_fabricator = create_agent_fabricator(complete_task)
            agent_fabricator(TASK)
    
    except Exception as e:
        print(f"Fabrication failed: {str(e)}")
        # Publish fabrication failure event
        publish_fabrication_event(
            workflow_id=workflow_id,
            event_type='agent.fabrication.failed',
            error=str(e)
        )
        raise


def lambda_handler(event, context):
    print(f"processing event {event}")
    for record in event['Records']:
        message_body = json.loads(record['body'])
        # print(f"Parsed message body: {json.dumps(message_body, indent=2)}")
        
        # Extract request type from SQS messageAttributes
        request_type = None
        if 'messageAttributes' in record and 'requestType' in record['messageAttributes']:
            request_type = record['messageAttributes']['requestType'].get('stringValue')
            print(f"Request type from messageAttributes: {request_type}")
        
        process_event(message_body, context, request_type=request_type)

if __name__ == "__main__":
    # Grab a record from your lambda and invoke, configuration will vary drastically
    process_event(
        {'agent_input': {'taskDetails': 'Create a capability to prepare and serve cheesecake dessert items'}, 'workflow_id': 'ed3b70b6-37c6-47fa-8f50-4eac0908345e',
            'agent_use_id': 'tooluse_L9uWo8_KR4mT70-876lzSA', 'node': 'fabricator'}, {}
    )