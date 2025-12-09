from chimerax.core.commands import Command, CmdDesc, RestOfLine
from chimerax.core import errors
from chimerax.core.commands.cli import register


@register("next")
def next_model(session):
	from chimerax.model_panel.tool import model_panel
	mp = model_panel(session, "Model Panel")
	mp._next_model()
 
 
@register("prev")
def previous_model(session):
	from chimerax.model_panel.tool import model_panel
	mp = model_panel(session, "Model Panel")
	mp._previous_model()


