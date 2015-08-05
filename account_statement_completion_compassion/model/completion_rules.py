# -*- encoding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2014 Compassion CH (http://www.compassion.ch)
#    Releasing children from poverty in Jesus' name
#    @author: Emanuel Cino <ecino@compassion.ch>
#
#    The licence is in the file __openerp__.py
#
##############################################################################

from openerp import api, exceptions, models, fields
# from openerp.addons.account_statement_base_completion.statement \
    # import ErrorTooManyPartner
from openerp.tools import DEFAULT_SERVER_DATE_FORMAT as DF
from openerp import netsvc
from openerp.addons.sponsorship_compassion.model.product import GIFT_CATEGORY, \
    GIFT_NAMES

from datetime import datetime
import time
import sys

class account_journal_completion(models.TransientModel):

    _inherit = 'account.journal'
    
    completion_rules = fields.Many2many('account.statement.completion.rule')

class account_bank_statement_import(models.TransientModel):

    _inherit = 'account.bank.statement.import'
    
    def _create_bank_statements(self, stmts_vals):
        statement_ids, notifs = super(
            account_bank_statement_import,
            self
        )._create_bank_statements(
            stmts_vals
        )
        for stmt_line in self.env['account.bank.statement'].browse(statement_ids).mapped('line_ids'):
            fields_update = stmt_line.journal_id.completion_rules.auto_complete(stmt_line)
            if fields_update:
                stmt_line.write(fields_update)
        return statement_ids, notifs

class StatementCompletionRule(models.Model):
    """ Add rules to complete account based on the BVR reference of the invoice
        and the reference of the partner."""

    _name = "account.statement.completion.rule"
    
    
    sequence = fields.Integer('Sequence',
                              help="Lower means parsed first.")
    name = fields.Char('Name', size=128)
    journal_ids = fields.Many2many(
        'account.journal',
        string='Related statement journal')
    function_to_call = fields.Selection('_get_functions', 'Method')

    def _get_functions(self):
        res = [
            ('get_from_partner_ref',
             'Compassion: From line reference '
             '(based on the partner reference)'),
            ('get_from_bvr_ref',
             'Compassion: From line reference '
             '(based on the BVR reference of the sponsor)'),
            ('lsv_dd_get_from_bvr_ref',
             'Compassion [LSV/DD]: From line reference '
             '(based on the BVR reference of the sponsor)'),
            ('get_from_amount',
             'Compassion: From line amount '
             '(based on the amount of the supplier invoice)'),
            ('get_from_lsv_dd', 'Compassion: Put LSV DD Credits in 1098'),
            ('get_from_move_line_ref',
             'Compassion: From line reference '
             '(based on previous move_line references)'),
            # ('get_sponsor_name',
             # 'Compassion: Match sponsor name '),
        ]
        return res

    @api.multi
    def auto_complete(self, stmt_line):
        """This method will execute all related rules, in their sequence order,
        to retrieve all the values returned by the first rules that will match.
        :param calls: list of lookup function name available in rules
        :param dict line: read of the concerned account.bank.statement.line
        :return:
            A dict of value that can be passed directly to the write method of
            the statement line or {}
           {'partner_id': value,
            'account_id: value,

            ...}
        """
        for rule in self.sorted(key=lambda r: r.sequence):
            method = getattr(self, rule.function_to_call)
            result = method(stmt_line)
            if result:
                result['already_completed'] = True
                return result
        return None

    def get_from_partner_ref(self, st_line):
        """
        If line ref match a partner reference, update partner and account
        Then, call the generic st_line method to complete other values.
        :param dict st_line: read of the concerned account.bank.statement.line
        :return:
            A dict of value that can be passed directly to the write method of
            the statement line or {}
           {'partner_id': value,
            'account_id' : value,
            ...}
        """
        ref = st_line.ref
        res = {}
        partner_obj = self.env['res.partner']
        partner_ids = partner_obj.search(
            [('ref', '=', ref[9:16]),
             ('is_company', '=', False)]).ids

        # Test that only one partner matches.
        partner = None
        if partner_ids:
            if len(partner_ids) == 1:
                partner = partner_obj.browse(partner_ids[0])
                # If we fall under this rule of completion, it means there is
                # no open invoice corresponding to the payment. We may need to
                # generate one depending on the payment type.
                res.update(
                    self._generate_invoice(st_line, partner))
                # Get the accounting partner (company)
                partner = partner_obj._find_accounting_partner(partner)
                res['partner_id'] = partner.id
                #res['account_id'] = partner.property_account_receivable.id
            else:
                raise exceptions.Warning(
                    ('Line named "%s" (Ref:%s) was matched by more '
                     'than one partner while looking on partners') %
                    (st_line['name'], st_line['ref']))
        return res

    def get_from_bvr_ref(self, st_line):
        """
        If line ref match an invoice BVR Reference, update partner and account
        Then, call the generic st_line method to complete other values.
        """
        ref = st_line.ref
        res = dict()
        partner = self._search_partner_by_bvr_ref(ref)

        if partner:
            partner_obj = self.env['res.partner']
            partner = partner_obj._find_accounting_partner(partner)
            res['partner_id'] = partner.id
            #res['account_id'] = partner.property_account_receivable.id

        return res

    def lsv_dd_get_from_bvr_ref(self, st_line):
        """
        If line ref match an invoice BVR Reference, update partner and account
        Then, call the generic st_line method to complete other values.
        For LSV/DD statements, search in all invoices.
        """
        ref = st_line.ref
        res = dict()
        partner = self._search_partner_by_bvr_ref(ref, True)

        if partner:
            partner_obj = self.env['res.partner']
            partner = partner_obj._find_accounting_partner(partner)
            res['partner_id'] = partner.id
            # res['account_id'] = partner.property_account_receivable.id

        return res

    def get_from_amount(self, st_line):
        """ If line amount match an open supplier invoice,
            update partner and account. """
        amount = st_line.amount
        res = {}
        # We check only for debit entries
        if amount < 0:
            invoice_obj = self.env['account.invoice']
            invoice_ids = invoice_obj.search(
                [('type', '=', 'in_invoice'), ('state', '=', 'open'),
                 ('amount_total', '=', abs(amount))])
            res = {}
            partner_obj = self.pool.get('res.partner')
            if invoices:
                if len(invoices) == 1:
                    partner = invoices.partner_id
                    res['partner_id'] = partner_obj._find_accounting_partner(
                        partner).id
                    #res['account_id'] = invoices.account_id.id
                else:
                    partner = invoices[0].partner_id
                    for invoice in invoices:
                        if invoice.partner_id.id != partner.id:
                            raise exceptions.Warning(
                                ('Line named "%s" (Ref:%s) was matched by '
                                 'more than one invoice while looking on open'
                                 ' supplier invoices') %
                                (st_line.name, st_line.ref))
                    res['partner_id'] = partner_obj._find_accounting_partner(
                        partner).id
                    #res['account_id'] = invoices[0].account_id.id

        return res

    def get_from_lsv_dd(self, st_line):
        """ If line is a LSV or DD credit, change the account to 1098. """
        label = st_line.name
        lsv_dd_strings = [u'BULLETIN DE VERSEMENT',
                          u'ORDRE DEBIT DIRECT',
                          u'Crèdit LSV']
        is_lsv_dd = False
        res = {}
        for credit_string in lsv_dd_strings:
            is_lsv_dd = is_lsv_dd or credit_string in label
        if is_lsv_dd:
            account_id = self.env['account.account'].search(
                [('code', '=', '1098')]).ids
            if account_id:
                res['account_id'] = account_id[0]

        return res

    def get_from_move_line_ref(self, st_line):
        ''' Update partner if same reference is found '''
        ref = st_line.ref
        res = {}
        partner = None

        # Search move lines
        move_line_obj = self.env['account.move.line']
        move_lines = move_line_obj.search(
            [('ref', '=', ref), ('partner_id', '!=', None)])
        if move_lines:
            partner = move_lines[0].partner_id

        if partner:
            partner_obj = self.env['res.partner']
            partner = partner_obj._find_accounting_partner(partner)
            res['partner_id'] = partner.id
            # res['account_id'] = partner.property_account_receivable.id

        return res

    def _generate_invoice(self, st_line, partner):
        """ Genereates an invoice corresponding to the statement line read
            in order to reconcile the corresponding move lines. """
        # Read data in english
        res = dict()
        product = self.with_context(lang='en_US')._find_product_id(
            st_line.ref)
        if not product:
            return res
        # Don't gengerate invoice if it's a Sponsor gift
        if product.categ_name == GIFT_CATEGORY:
            res['name'] = product.name
            contract_obj = self.env['recurring.contract']
            contract_number = int(st_line.ref[16:21])
            contract = contract_obj.search(
                ['|',
                 ('partner_id', '=', partner.id),
                 ('correspondant_id', '=', partner.id),
                 ('num_pol_ga', '=', contract_number),
                 ('state', '!=', 'draft')
                ])
            if len(contract) == 1:
                # Retrieve the birthday of child
                birthdate = ""
                if product.name == GIFT_NAMES[0]:
                    birthdate = contract.child_id.birthdate
                    res['child_birthdate'] = birthdate
                    birthdate = datetime.strptime(birthdate, DF).strftime(
                        "%d %b")
                res['name'] += "[" + contract.child_id.code
                res['name'] += " (" + birthdate + ")]" if birthdate else "]"
            else:
                res['name'] += " [Child not found] "
            return res
        
        # Setup invoice data
        invoicer_id = st_line.statement_id.recurring_invoicer_id.id
        if not invoicer_id:
            invoicer_id = self.env['recurring.invoicer'].create(
                {'source': st_line.statement_id._name}).id
            st_line.statement_id.write({'recurring_invoicer_id': invoicer_id})
        inv_data = {
            'account_id': partner.property_account_receivable.id,
            'type': 'out_invoice',
            'partner_id': partner.id,
            'journal_id': st_line.statement_id.journal_id.id,
            'date_invoice': st_line.date,
            'payment_term': 1,  # Immediate payment
            'bvr_reference': st_line.ref,
            'recurring_invoicer_id': invoicer_id,
        }

        # Create invoice and generate invoice lines
        invoice_obj = self.env['account.invoice']
        invoice = invoice_obj.with_context(lang='en_US').create(inv_data)

        res.update(self._generate_invoice_line(
            invoice.id, product, st_line, partner.id))

        if product.categ_name != GIFT_CATEGORY:
            # Validate the invoice
            wf_service = netsvc.LocalService('workflow')
            wf_service.trg_validate(
                self.env.user.id, 'account.invoice',
                invoice.id, 'invoice_open', self.env.cr)

        return res

    def _find_product_id(self, ref):
        """ Finds what kind of payment it is,
            based on the reference of the statement line. """
        product_obj = self.env['product.product']
        payment_type = int(ref[21])
        product = 0
        if payment_type in range(1, 6):
            # Sponsor Gift
            products = product_obj.search(
                [('name_template', '=', GIFT_NAMES[payment_type-1])])
            product = products[0] if products else 0
        elif payment_type in range(6, 8):
            # Fund donation
            products = product_obj.search(
                [('gp_fund_id', '=', int(ref[22:26]))])
            product = products[0] if products else 0

        return product

    def _generate_invoice_line(self, invoice_id, product, st_line, partner_id):
        inv_line_data = {
            'name': product.name,
            'account_id': product.property_account_income.id,
            'price_unit': st_line.amount,
            'price_subtotal': st_line.amount,
            'quantity': 1,
            'uos_id': False,
            'product_id': product.id or False,
            'invoice_id': invoice_id,
        }

        res = {}

        # Define analytic journal
        analytic = self.env['account.analytic.default'].account_get(
            product.id, partner_id, time.strftime('%Y-%m-%d'))
        if analytic and analytic.analytic_id:
            inv_line_data['account_analytic_id'] = analytic.analytic_id.id

        res['name'] = product.name

        self.env['account.invoice.line'].create(inv_line_data)

        return res

    def _search_partner_by_bvr_ref(self, bvr_ref,
                                   search_old_invoices=False):
        """ Finds a partner given its bvr reference. """
        partner = None
        contract_group_obj = self.env['recurring.contract.group']
        contract_groups = contract_group_obj.search(
            [('bvr_reference', '=', bvr_ref)])
        if contract_groups:
            partner = contract_groups[0].partner_id
        else:
            # Search open Customer Invoices (with field 'bvr_reference' set)
            invoice_obj = self.env['account.invoice']
            invoice_search = [
                ('bvr_reference', '=', bvr_ref),
                ('state', '=', 'open')]
            if search_old_invoices:
                invoice_search[1] = ('state', 'in', ('open', 'cancel',
                                                     'paid'))
            invoices = invoice_obj.search(invoice_search)
            if not invoices:
                # Search open Supplier Invoices (with field 'reference_type'
                # set to BVR)
                invoices = invoice_obj.search([
                    ('reference_type', '=', 'bvr'),
                    ('reference', '=', bvr_ref),
                    ('state', '=', 'open')])
            if invoices:
                partner = invoices[0].partner_id
        return partner

    def get_sponsor_name(self, st_line):
        res = dict()
        label = st_line.label

        # Fix encoding problem
        reload(sys)
        sys.setdefaultencoding('UTF8')

        query_find_partner = (
            "SELECT id from res_partner "
            "WHERE ('{0}' ILIKE concat('%', trim(name), '%') "
            "OR '{0}' ILIKE "
            "CASE WHEN bank_statement_label <> '' THEN "
            "   concat('%',"
            "          trim(bank_statement_label),"
            "          '%')"
            "ELSE name "
            "END) "
            "AND name NOT ILIKE '%Compassion%'"
            .format(label.replace("'", "''")))
        self.env.cr.execute(query_find_partner)
        partner_ids = self.env.cr.fetchall()
        if len(partner_ids) == 1:
            res['partner_id'] = partner_ids[0][0]
        elif partner_ids:
            raise exceptions.Warning(
                ('Line named "%s" was matched by '
                 'more than one sponsor') % st_line['name'])

        return res