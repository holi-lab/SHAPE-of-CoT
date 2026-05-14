"""
Pydantic schemas for structured LLM output validation
"""
from pydantic import BaseModel, Field, field_validator
from typing import List


class SingleAnnotation(BaseModel):
    """Schema for a single heuristic or non-heuristic tag"""
    code: str = Field(
        ..., 
        description="Heuristic or non-heuristic code (e.g., 'H4', 'N2')"
    )
    evidence: str = Field(
        ..., 
        description="Specific sentence or phrase directly quoted from the chunk that serves as evidence"
    )
    reasoning: str = Field(
        ..., 
        description="Brief explanation of why this code applies"
    )
    
    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        """Ensure codes are valid heuristic or non-heuristic codes"""
        valid_codes = {
            'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'H7', 'H8', 'H9', 'H10', 'H11',
            'N1', 'N2', 'N3', 'N4'
        }
        
        # Strip whitespace and convert to uppercase
        code = v.strip().upper()
        if code not in valid_codes:
            # Allow sub-codes like H1a, H13b, etc - strip the letter suffix
            base_code = ''.join(c for c in code if not c.islower())
            if base_code not in valid_codes:
                raise ValueError(f"Invalid heuristic code: {code}. Must be one of {valid_codes}")
        
        return v.strip().upper()
    
    @field_validator('reasoning', 'evidence')
    @classmethod
    def validate_text_fields(cls, v):
        """Ensure text fields are not empty"""
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class ChunkAnnotation(BaseModel):
    """Schema for chunk annotation response from LLM"""
    annotations: List[SingleAnnotation] = Field(
        ..., 
        description="List of annotations for this chunk"
    )
    has_answer: bool = Field(
        ...,
        description="True if the model has produced a final answer in this chunk, otherwise False"
    )
    extracted_answer: str | None = Field(
        None,
        description="The final answer produced by the model, if has_answer is True."
    )


class SentenceAnnotation(BaseModel):
    """Schema for sentence annotation response from LLM"""
    index: str = Field(..., description="Index of the sentence")
    reason: str = Field(..., description="Short reason for the classification")
    category: str = Field(..., description="Fine-grained class of the sentence")
    
    @field_validator('index')
    @classmethod
    def validate_index(cls, v):
        """Extract numeric index from string"""
        # Handle formats like '[1]', '1', etc.
        import re
        match = re.search(r'\d+', str(v))
        if match:
            return match.group()
        raise ValueError(f"Invalid index format: {v}")


class SentenceBatchAnnotation(BaseModel):
    """Schema for batch sentence annotation response"""
    sentences: List[SentenceAnnotation] = Field(
        ..., 
        description="List of annotated sentences"
    )
