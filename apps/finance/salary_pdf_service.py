"""
Salary Slip PDF Generation Service
Generate professional PDF salary slips
SOFT-CODED for easy template customization
"""
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from django.conf import settings
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class SalarySlipPDFService:
    """
    Service class for generating salary slip PDFs
    SOFT-CODED: Template and styling can be easily customized
    """
    
    # SOFT-CODED: PDF Configuration
    PDF_CONFIG = {
        'page_size': A4,
        'title': 'SALARY SLIP',
        'company_name': 'RADAI - Rejlers Engineering',
        'company_address': 'Abu Dhabi, United Arab Emirates',
        'logo_path': None,  # Path to company logo (optional)
        'header_color': colors.HexColor('#1e40af'),  # Blue
        'table_header_color': colors.HexColor('#3b82f6'),  # Light blue
        'border_color': colors.HexColor('#e5e7eb'),  # Gray
    }
    
    def __init__(self):
        self.logger = logger
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
    
    def _setup_custom_styles(self):
        """Setup custom paragraph styles"""
        self.styles.add(ParagraphStyle(
            name='CompanyName',
            parent=self.styles['Heading1'],
            fontSize=18,
            textColor=self.PDF_CONFIG['header_color'],
            alignment=TA_CENTER,
            spaceAfter=6
        ))
        
        self.styles.add(ParagraphStyle(
            name='Title',
            parent=self.styles['Heading2'],
            fontSize=14,
            textColor=colors.black,
            alignment=TA_CENTER,
            spaceAfter=12
        ))
        
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading3'],
            fontSize=11,
            textColor=self.PDF_CONFIG['header_color'],
            spaceAfter=6
        ))
    
    def generate_pdf(self, salary_slip):
        """
        Generate PDF for a salary slip
        
        Args:
            salary_slip: SalarySlip instance
        
        Returns:
            str: Path to generated PDF file
        """
        try:
            # Create PDF directory if not exists
            pdf_dir = os.path.join(settings.MEDIA_ROOT, 'salary_slips', str(salary_slip.year), f"{salary_slip.month:02d}")
            os.makedirs(pdf_dir, exist_ok=True)
            
            # Generate filename
            filename = f"{salary_slip.slip_number}.pdf"
            pdf_path = os.path.join(pdf_dir, filename)
            
            # Create PDF document
            doc = SimpleDocTemplate(
                pdf_path,
                pagesize=self.PDF_CONFIG['page_size'],
                rightMargin=0.75*inch,
                leftMargin=0.75*inch,
                topMargin=0.75*inch,
                bottomMargin=0.75*inch
            )
            
            # Build PDF content
            story = []
            
            # Header
            story.extend(self._build_header(salary_slip))
            story.append(Spacer(1, 0.3*inch))
            
            # Employee Information
            story.extend(self._build_employee_info(salary_slip))
            story.append(Spacer(1, 0.2*inch))
            
            # Earnings Table
            story.extend(self._build_earnings_table(salary_slip))
            story.append(Spacer(1, 0.2*inch))
            
            # Deductions Table
            story.extend(self._build_deductions_table(salary_slip))
            story.append(Spacer(1, 0.2*inch))
            
            # Net Salary
            story.extend(self._build_net_salary(salary_slip))
            story.append(Spacer(1, 0.3*inch))
            
            # Footer
            story.extend(self._build_footer())
            
            # Build PDF
            doc.build(story)
            
            self.logger.info(f"PDF generated successfully: {pdf_path}")
            
            # Return relative path
            return pdf_path.replace(settings.MEDIA_ROOT, '').lstrip('/')
            
        except Exception as e:
            self.logger.error(f"PDF generation failed: {str(e)}")
            raise

    def generate_pdf_bytes(self, salary_slip) -> 'io.BytesIO':
        """
        Generate PDF for a salary slip and return the content as a BytesIO buffer.
        Used by the Celery task so the PDF can be uploaded directly to S3
        without touching the local filesystem.
        """
        import io
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=self.PDF_CONFIG['page_size'],
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch,
        )
        story = []
        story.extend(self._build_header(salary_slip))
        story.append(Spacer(1, 0.3*inch))
        story.extend(self._build_employee_info(salary_slip))
        story.append(Spacer(1, 0.2*inch))
        story.extend(self._build_earnings_table(salary_slip))
        story.append(Spacer(1, 0.2*inch))
        story.extend(self._build_deductions_table(salary_slip))
        story.append(Spacer(1, 0.2*inch))
        story.extend(self._build_net_salary(salary_slip))
        story.append(Spacer(1, 0.3*inch))
        story.extend(self._build_footer())
        doc.build(story)
        buf.seek(0)
        return buf

    def _build_header(self, salary_slip):
        """Build PDF header with company info"""
        elements = []
        
        # Company name
        elements.append(Paragraph(
            self.PDF_CONFIG['company_name'],
            self.styles['CompanyName']
        ))
        
        # Company address
        elements.append(Paragraph(
            self.PDF_CONFIG['company_address'],
            self.styles['Normal']
        ))
        
        # Title
        elements.append(Spacer(1, 0.2*inch))
        elements.append(Paragraph(
            self.PDF_CONFIG['title'],
            self.styles['Title']
        ))
        
        # Slip details
        month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                      'July', 'August', 'September', 'October', 'November', 'December']
        slip_info = f"<b>Month:</b> {month_names[salary_slip.month-1]} {salary_slip.year} | <b>Slip No:</b> {salary_slip.slip_number}"
        elements.append(Paragraph(slip_info, self.styles['Normal']))
        
        return elements
    
    def _build_employee_info(self, salary_slip):
        """Build employee information section"""
        elements = []
        
        employee = salary_slip.employee_salary_info
        user = employee.user
        
        data = [
            ['Employee Name:', user.get_full_name() or user.email, 'Employee ID:', employee.employee_id],
            ['Department:', employee.department or 'N/A', 'Designation:', employee.designation or 'N/A'],
            ['Bank Account:', employee.account_number or 'N/A', 'IBAN:', employee.iban or 'N/A'],
            ['Working Days:', str(salary_slip.working_days), 'Present Days:', str(salary_slip.present_days)],
        ]
        
        table = Table(data, colWidths=[1.5*inch, 2.5*inch, 1.5*inch, 2*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.PDF_CONFIG['border_color']),
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f3f4f6')),
            ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#f3f4f6')),
        ]))
        
        elements.append(table)
        
        return elements
    
    def _build_earnings_table(self, salary_slip):
        """Build earnings/allowances table"""
        elements = []
        
        elements.append(Paragraph('EARNINGS', self.styles['SectionHeader']))
        
        data = [['Description', 'Amount']]
        data.append(['Basic Salary', f"{salary_slip.currency} {salary_slip.basic_salary:,.2f}"])
        
        # Add allowances
        for name, details in salary_slip.allowances_breakdown.items():
            amount = float(details['amount'])
            data.append([name, f"{salary_slip.currency} {amount:,.2f}"])
        
        # Gross total
        data.append(['GROSS SALARY', f"{salary_slip.currency} {salary_slip.gross_salary:,.2f}"])
        
        table = Table(data, colWidths=[5*inch, 2*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BACKGROUND', (0, 0), (-1, 0), self.PDF_CONFIG['table_header_color']),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.PDF_CONFIG['border_color']),
            ('BACKGROUND', (-1, -1), (-1, -1), colors.HexColor('#dbeafe')),
            ('FONTNAME', (-1, -1), (-1, -1), 'Helvetica-Bold'),
        ]))
        
        elements.append(table)
        
        return elements
    
    def _build_deductions_table(self, salary_slip):
        """Build deductions table"""
        elements = []
        
        elements.append(Paragraph('DEDUCTIONS', self.styles['SectionHeader']))
        
        data = [['Description', 'Amount']]
        
        # Add tax
        if salary_slip.tax_deduction > 0:
            data.append(['Income Tax', f"{salary_slip.currency} {salary_slip.tax_deduction:,.2f}"])
        
        # Add deductions
        for name, details in salary_slip.deductions_breakdown.items():
            amount = float(details['amount'])
            data.append([name, f"{salary_slip.currency} {amount:,.2f}"])
        
        # Total deductions
        data.append(['TOTAL DEDUCTIONS', f"{salary_slip.currency} {salary_slip.total_deductions:,.2f}"])
        
        table = Table(data, colWidths=[5*inch, 2*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BACKGROUND', (0, 0), (-1, 0), self.PDF_CONFIG['table_header_color']),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.PDF_CONFIG['border_color']),
            ('BACKGROUND', (-1, -1), (-1, -1), colors.HexColor('#fecaca')),
            ('FONTNAME', (-1, -1), (-1, -1), 'Helvetica-Bold'),
        ]))
        
        elements.append(table)
        
        return elements
    
    def _build_net_salary(self, salary_slip):
        """Build net salary section"""
        elements = []
        
        data = [['NET SALARY (Payable)', f"{salary_slip.currency} {salary_slip.net_salary:,.2f}"]]
        
        table = Table(data, colWidths=[5*inch, 2*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 12),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#10b981')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.white),
        ]))
        
        elements.append(table)
        
        return elements
    
    def _build_footer(self):
        """Build PDF footer"""
        elements = []
        
        footer_text = """
        <i>This is a computer generated salary slip and does not require a signature.</i><br/>
        <i>For any queries, please contact HR department.</i>
        """
        
        elements.append(Paragraph(footer_text, self.styles['Normal']))
        
        return elements
