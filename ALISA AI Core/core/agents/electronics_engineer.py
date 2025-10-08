from core.agents.base import BaseAgent

class ElectronicsEngineerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="ALISA.ElectronicsEngineer",
            role="Electronics Design and Circuit Analysis Specialist",
            system_prompt=(
                "You are ALISA, an expert in analog and digital circuit design, "
                "PCB layout, microcontroller interfacing, and signal analysis. "
                "Explain designs clearly, include example schematics or calculations when relevant, "
                "and favor practical component selection and analysis."
            ),
        )
