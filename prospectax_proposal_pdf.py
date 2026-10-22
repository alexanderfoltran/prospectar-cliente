"""Propostas editáveis no sistema, exportadas para envio manual em PDF."""
import html,io,re
from datetime import datetime,timedelta,timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,KeepTogether

def render_pdf(proposal):
    out=io.BytesIO();navy=colors.HexColor('#133047');teal=colors.HexColor('#147a78');muted=colors.HexColor('#61727f')
    doc=SimpleDocTemplate(out,pagesize=A4,rightMargin=21*mm,leftMargin=21*mm,topMargin=31*mm,bottomMargin=24*mm,title=proposal['subject'],author=proposal['seller'])
    title=ParagraphStyle('title',fontName='Helvetica-Bold',fontSize=21,leading=26,textColor=navy,spaceAfter=14)
    body=ParagraphStyle('body',fontName='Helvetica',fontSize=10.5,leading=16,textColor=navy,spaceAfter=10)
    label=ParagraphStyle('label',fontName='Helvetica-Bold',fontSize=9,leading=13,textColor=teal,spaceAfter=7)
    small=ParagraphStyle('small',fontName='Helvetica',fontSize=9,leading=13,textColor=muted,spaceAfter=7)
    escape=lambda s:html.escape(str(s)).replace('\n','<br/>')
    def page(canvas,document):
        width,height=A4;canvas.saveState();canvas.setFillColor(teal);canvas.rect(0,height-8*mm,width,8*mm,fill=1,stroke=0)
        canvas.setFont('Helvetica-Bold',10);canvas.setFillColor(navy);canvas.drawString(21*mm,height-20*mm,'PROPOSTA COMERCIAL')
        canvas.setStrokeColor(colors.HexColor('#d7e1e5'));canvas.line(21*mm,18*mm,width-21*mm,18*mm)
        canvas.setFillColor(muted);canvas.setFont('Helvetica',8)
        canvas.drawString(21*mm,12*mm,'Preparada no ProspectaX | '+('Rascunho - revisar antes de enviar' if proposal.get('status')!='reviewed' else 'Revisada pelo usuário'))
        canvas.drawRightString(width-21*mm,12*mm,'Página '+str(document.page));canvas.restoreState()
    target=proposal['target'];stamp=proposal.get('created_at','')
    try:stamp=datetime.fromisoformat(stamp).astimezone(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y')
    except ValueError:stamp=''
    flow=[Paragraph(escape(proposal['subject']),title),Paragraph('PARA '+escape(target['name']),label),Paragraph(escape(' · '.join(v for v in [target.get('city',''),target.get('uf',''),stamp] if v)),small),Spacer(1,5*mm)]
    for block in re.split(r'\n\s*\n',proposal['body']):
        if block.strip():flow.append(Paragraph(escape(block),body))
    flow.append(Spacer(1,7*mm));flow.append(KeepTogether([Paragraph('APRESENTADA POR',label),Paragraph(escape(proposal['seller']),body)]))
    doc.build(flow,onFirstPage=page,onLaterPages=page)
    return out.getvalue()
