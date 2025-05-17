from metalcloud.service import McpService, MethodType, Method, Parameter, ParameterType, Auth, AuthType

# Define the MCP service for answering questions
question_answering_service = McpService(
    name="QuestionAnsweringService",
    description="A service that allows users to ask questions and get answers from an AI assistant",
    base_path="/qa",
    auth=Auth(
        type=AuthType.API_KEY,
        name="x-api-key",
        location="header",
        description="API key for authentication"
    ),
    methods=[
        Method(
            name="ask_question",
            description="Ask a question and get an answer from the AI assistant",
            path="/ask",
            method_type=MethodType.POST,
            parameters=[
                Parameter(
                    name="question",
                    description="The question to ask the AI assistant",
                    required=True,
                    parameter_type=ParameterType.BODY
                ),
            ],
            requires_auth=True,
            response_description="Answer from the AI assistant"
        ),
        Method(
            name="health",
            description="Check if the service is healthy",
            path="/health",
            method_type=MethodType.GET,
            parameters=[],
            requires_auth=False,
            response_description="Health status of the service"
        ),
        Method(
            name="generate_api_key",
            description="Generate a new API key for a user (admin only)",
            path="/generate-api-key/{email}",
            method_type=MethodType.GET,
            parameters=[
                Parameter(
                    name="email",
                    description="Email of the user to generate an API key for",
                    required=True,
                    parameter_type=ParameterType.PATH
                ),
                Parameter(
                    name="admin_key",
                    description="Admin key for authentication",
                    required=True,
                    parameter_type=ParameterType.QUERY
                ),
            ],
            requires_auth=False,
            response_description="Newly generated API key"
        ),
        Method(
            name="view_api_key",
            description="View a user's API key (admin only)",
            path="/view-api-key/{email}",
            method_type=MethodType.GET,
            parameters=[
                Parameter(
                    name="email",
                    description="Email of the user to view API key for",
                    required=True,
                    parameter_type=ParameterType.PATH
                ),
                Parameter(
                    name="admin_key",
                    description="Admin key for authentication",
                    required=True,
                    parameter_type=ParameterType.QUERY
                ),
            ],
            requires_auth=False,
            response_description="User's API key information"
        )
    ]
) 
