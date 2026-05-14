"""
Pydantic schemas for structured LLM output validation
"""
import re
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List


class SingleAnnotation(BaseModel):
    """Schema for a single heuristic or non-heuristic tag"""
    code: str = Field(
        ...,
        description="Heuristic or non-heuristic code (e.g., 'H4a', 'N2'). "
                    "Use lowercase suffix for sub-codes (e.g., H4a, H13f)."
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
        """Ensure codes match H<number>[optional lowercase suffix] or N<number>[optional lowercase suffix].
        Suffix must be lowercase (e.g. H4a, H13f). Normalizes prefix to uppercase."""
        code = v.strip()
        # Normalize: uppercase prefix letter, lowercase suffix
        # e.g. H11A -> H11a, h4A -> H4a
        match = re.fullmatch(r'([HNhn])(\d+)([a-zA-Z]?)', code)
        if not match:
            raise ValueError(
                f"Invalid heuristic code: '{code}'. "
                "Must match pattern H<n>[suffix] or N<n>[suffix] (e.g. H1, H13a, N2)."
            )
        prefix, number, suffix = match.groups()
        return f"{prefix.upper()}{number}{suffix.lower()}"
    
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

    @model_validator(mode='after')
    def heuristic_first(self):
        """If any H code is present, remove all N codes (heuristic-first rule)."""
        codes = [ann.code for ann in self.annotations]
        has_h = any(c.startswith('H') for c in codes)
        if has_h:
            self.annotations = [ann for ann in self.annotations if ann.code.startswith('H')]
        return self


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
