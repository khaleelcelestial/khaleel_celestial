"""
Visualize the LangGraph pipeline and save as an image.
"""
import os
from pathlib import Path

# Load environment first
from core.load_env import load_dotenv
load_dotenv()

from core.graph import app

def visualize_graph():
    """Generate and save the graph visualization."""
    
    # Create output directory
    output_dir = Path("graph_builder_image")
    output_dir.mkdir(exist_ok=True)
    
    # Generate the graph image
    try:
        # LangGraph's built-in visualization
        png_data = app.get_graph().draw_mermaid_png()
        
        output_path = output_dir / "langgraph_pipeline.png"
        with open(output_path, "wb") as f:
            f.write(png_data)
        
        print(f"✅ Graph visualization saved to: {output_path.absolute()}")
        print(f"   Open this file to see the complete pipeline flow!")
        
    except Exception as e:
        print(f"❌ Could not generate PNG (may need additional dependencies): {e}")
        print("\nTrying Mermaid text format instead...")
        
        try:
            # Fallback to Mermaid text format
            mermaid_text = app.get_graph().draw_mermaid()
            
            output_path = output_dir / "langgraph_pipeline.mmd"
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(mermaid_text)
            
            print(f"✅ Graph Mermaid diagram saved to: {output_path.absolute()}")
            print(f"   You can visualize this at: https://mermaid.live/")
            
            # Also save as markdown for easy viewing
            md_path = output_dir / "langgraph_pipeline.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# LangGraph Pipeline Visualization\n\n")
                f.write("```mermaid\n")
                f.write(mermaid_text)
                f.write("\n```\n\n")
                f.write("## Graph Structure\n\n")
                f.write("### Nodes:\n")
                f.write("1. **Planner** - Analyzes requirements and creates execution plan\n")
                f.write("2. **Database** - Generates schema and OpenAPI spec\n")
                f.write("3. **Supervisor** - Routes work to appropriate agents (20-round max)\n")
                f.write("4. **Backend** - Writes backend code (FastAPI/Node.js)\n")
                f.write("5. **Frontend** - Writes frontend code (React/Vite)\n")
                f.write("6. **Testing** - Runs static checks and code review\n")
                f.write("7. **Deployment** - Packages and deploys the project\n\n")
                f.write("### Flow:\n")
                f.write("- **New Build**: Entry → Planner → Database → Supervisor → [Backend/Frontend/Testing/Deployment] → Supervisor (loop) → Done\n")
                f.write("- **Update Mode**: Entry → Supervisor (skips Planner/Database) → [agents] → Done\n")
            
            print(f"✅ Markdown with graph saved to: {md_path.absolute()}")
            
        except Exception as e2:
            print(f"❌ Could not generate Mermaid format either: {e2}")
            print("\nCreating text description instead...")
            
            # Final fallback: text description
            text_path = output_dir / "langgraph_pipeline.txt"
            with open(text_path, "w", encoding="utf-8") as f:
                f.write("LANGGRAPH PIPELINE STRUCTURE\n")
                f.write("=" * 50 + "\n\n")
                f.write("NODES:\n")
                f.write("------\n")
                f.write("1. Planner      - Analyzes user request, generates plan\n")
                f.write("2. Database     - Creates schema + OpenAPI spec\n")
                f.write("3. Supervisor   - Routes to appropriate agent (max 20 rounds)\n")
                f.write("4. Backend      - Tool-calling agent with file access (write to backend/)\n")
                f.write("5. Frontend     - Tool-calling agent with file access (write to frontend/)\n")
                f.write("6. Testing      - Runs checks, reviews code (read-only)\n")
                f.write("7. Deployment   - Packages project, runs docker compose\n\n")
                f.write("FLOW:\n")
                f.write("-----\n")
                f.write("New Build:\n")
                f.write("  Entry Point (conditional)\n")
                f.write("    ↓\n")
                f.write("  Planner\n")
                f.write("    ↓\n")
                f.write("  Database\n")
                f.write("    ↓\n")
                f.write("  Supervisor ←──────────┐\n")
                f.write("    ↓                    │\n")
                f.write("  [Backend/Frontend/     │\n")
                f.write("   Testing/Deployment] ──┘\n")
                f.write("    ↓\n")
                f.write("  Done (when supervisor decides)\n\n")
                f.write("Update Mode:\n")
                f.write("  Entry Point (skips to Supervisor)\n")
                f.write("    ↓\n")
                f.write("  Supervisor ←──────────┐\n")
                f.write("    ↓                    │\n")
                f.write("  [Backend/Frontend/     │\n")
                f.write("   Testing/Deployment] ──┘\n")
                f.write("    ↓\n")
                f.write("  Done\n\n")
                f.write("KEY FEATURES:\n")
                f.write("-------------\n")
                f.write("- Supervisor has 20-round backstop to prevent infinite loops\n")
                f.write("- Consecutive failure detection (stops after 3 'all providers exhausted')\n")
                f.write("- Agents have real tool access: read/write files, run commands\n")
                f.write("- State is checkpointed and can resume from any point\n")
                f.write("- Project registry saves progress even on failure\n")
            
            print(f"✅ Text description saved to: {text_path.absolute()}")

if __name__ == "__main__":
    print("Generating LangGraph visualization...\n")
    visualize_graph()
