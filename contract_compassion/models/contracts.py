# -*- encoding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2014 Compassion CH (http://www.compassion.ch)
#    Releasing children from poverty in Jesus' name
#    @author: Cyril Sester, Emanuel Cino
#
#    The licence is in the file __openerp__.py
#
##############################################################################

import logging

from openerp import models, fields, api, _
from openerp.exceptions import UserError
from openerp.addons.connector.queue.job import job, related_action
from openerp.addons.connector.session import ConnectorSession

logger = logging.getLogger(__name__)


class ContractGroup(models.Model):
    _inherit = 'recurring.contract.group'

    def _get_gen_states(self):
        return ['active', 'waiting']


class RecurringContract(models.Model):
    _inherit = "recurring.contract"
    _order = 'start_date desc'

    ##########################################################################
    #                                 FIELDS                                 #
    ##########################################################################
    child_id = fields.Many2one(
        'compassion.child', 'Sponsored child', readonly=True, copy=False,
        states={'draft': [('readonly', False)],
                'waiting': [('readonly', False)],
                'mandate': [('readonly', False)]}, ondelete='restrict',
        track_visibility='onchange')
    project_id = fields.Many2one('compassion.project', 'Project',
                                 related='child_id.project_id')
    child_name = fields.Char(
        'Sponsored child name', related='child_id.name', readonly=True)
    child_code = fields.Char(
        'Sponsored child code', related='child_id.local_id', readonly=True)
    activation_date = fields.Date(readonly=True, copy=False)
    is_active = fields.Boolean(
        'Contract Active', compute='compute_active', store=True,
        help="It indicates that the first invoice has been paid and the "
             "contract was activated.")
    # Field used for identifying gifts from sponsor
    commitment_number = fields.Integer(
        'Partner Contract Number', required=True, copy=False,
        oldname='num_pol_ga'
    )
    end_reason = fields.Selection('get_ending_reasons')
    months_paid = fields.Integer(compute='_compute_months_paid')
    origin_id = fields.Many2one(
        'recurring.contract.origin', 'Origin', ondelete='restrict',
        track_visibility='onchange')
    channel = fields.Selection('_get_channels')
    parent_id = fields.Many2one(
        'recurring.contract', 'Previous sponsorship',
        track_visibility='onchange')
    name = fields.Char(compute='_set_name', store=True)
    partner_id = fields.Many2one(
        'res.partner', 'Partner', required=True,
        readonly=False, states={'terminated': [('readonly', True)]},
        ondelete='restrict', track_visibility='onchange')
    type = fields.Selection('_get_type', required=True, default='O')
    group_freq = fields.Char(
        string='Payment frequency', compute='_set_frequency', readonly=True)

    ##########################################################################
    #                             FIELDS METHODS                             #
    ##########################################################################
    @api.model
    def _get_states(self):
        """ Add a waiting and a cancelled state """
        states = super(RecurringContract, self)._get_states()
        states.insert(1, ('waiting', _('Waiting Payment')))
        states.insert(len(states), ('cancelled', _('Cancelled')))
        return states

    @api.multi
    @api.depends('partner_id', 'child_id')
    def _set_name(self):
        """ Gives a friendly name for a sponsorship """
        for contract in self:
            if contract.partner_id.ref or contract.reference:
                name = contract.partner_id.ref or contract.reference
                if contract.child_id:
                    name += ' - ' + contract.child_code
                elif contract.contract_line_ids:
                    name += ' - ' + contract.contract_line_ids[
                        0].product_id.name
                contract.name = name

    @api.multi
    @api.depends('activation_date', 'state')
    def compute_active(self):
        for contract in self:
            contract.is_active = bool(contract.activation_date) and \
                contract.state not in ('terminated', 'cancelled')

    def get_ending_reasons(self):
        """Returns all the ending reasons of sponsorships"""
        return [
            ('2', _("Mistake from our staff")),
            ('3', _("Death of partner")),
            ('4', _("Moved to foreign country")),
            ('5', _("Not satisfied")),
            ('6', _("Doesn't pay")),
            ('8', _("Personal reasons")),
            ('9', _("Never paid")),
            ('12', _("Financial reasons")),
            ('25', _("Not given")),
        ]

    def _get_channels(self):
        """Returns the available channel through the new sponsor
        reached Compassion.
        """
        return [
            ('postal', _("By mail")),
            ('direct', _("Direct")),
            ('email', _("By e-mail")),
            ('internet', _("From the website")),
            ('phone', _("By phone")),
            ('payment', _("Payment")),
        ]

    def _get_type(self):
        return [('O', _('General'))]

    @api.multi
    def _set_frequency(self):
        frequencies = {
            '1 month': _('Monthly'),
            '2 month': _('Bimonthly'),
            '3 month': _('Quarterly'),
            '4 month': _('Four-monthly'),
            '6 month': _('Bi-annual'),
            '12 month': _('Annual'),
            '1 year': _('Annual'),
        }
        for contract in self:
            if contract.type == 'S':
                recurring_value = contract.group_id.advance_billing_months
                recurring_unit = _('month')
            else:
                recurring_value = contract.group_id.recurring_value
                recurring_unit = contract.group_id.recurring_unit
            frequency = "{0} {1}".format(recurring_value, recurring_unit)
            if frequency in frequencies:
                frequency = frequencies[frequency]
            else:
                frequency = _('every') + ' ' + frequency.lower()
            contract.group_freq = frequency

    @api.multi
    def _compute_months_paid(self):
        """This is a query returning the number of months paid for a
        sponsorship."""
        self._cr.execute(
            "SELECT c.id as contract_id, "
            "12 * (EXTRACT(year FROM next_invoice_date) - "
            "      EXTRACT(year FROM current_date))"
            " + EXTRACT(month FROM c.next_invoice_date) - 1"
            " - COALESCE(due.total, 0) as paidmonth "
            "FROM recurring_contract c left join ("
            # Open invoices to find how many months are due
            "   select contract_id, count(distinct invoice_id) as total "
            "   from account_invoice_line l join product_product p on "
            "       l.product_id = p.id "
            "   where state='open' and "
            # Exclude gifts from count
            "   categ_name != 'Sponsor gifts'"
            "   group by contract_id"
            ") due on due.contract_id = c.id "
            "WHERE c.id in (%s)" % ",".join([str(id) for id in self.ids])
        )
        res = self._cr.dictfetchall()
        dict_contract_id_paidmonth = {
            row['contract_id']: int(row['paidmonth'] or 0) for row in res}
        for contract in self:
            contract.months_paid = dict_contract_id_paidmonth[contract.id]

    ##########################################################################
    #                              ORM METHODS                               #
    ##########################################################################
    @api.model
    def create(self, vals):
        if 'commitment_number' not in vals:
            partner_id = vals.get('partner_id')
            if partner_id:
                other_nums = self.search([
                    ('partner_id', '=', partner_id)
                ]).mapped('commitment_number')
                vals['commitment_number'] = max(other_nums or [-1]) + 1
            else:
                vals['commitment_number'] = 1
        return super(RecurringContract, self).create(vals)

    @api.multi
    def write(self, vals):
        """ Perform various checks when a contract is modified. """
        # Write the changes
        res = super(RecurringContract, self).write(vals)

        if 'group_id' in vals or 'partner_id' in vals:
            self._on_group_id_changed()

        return res

    ##########################################################################
    #                             PUBLIC METHODS                             #
    ##########################################################################
    @api.multi
    def invoice_unpaid(self, invoice):
        """ Hook when invoice is unpaid """
        pass

    @api.multi
    def invoice_paid(self, invoice):
        """ Activate contract if it is waiting for payment. """
        activate_contracts = self.filtered(lambda c: c.state == 'waiting')
        # Cancel the old invoices if a contract is activated
        activate_contracts._cancel_old_invoices(
            invoice.partner_id.id, invoice.date_invoice)
        activate_contracts.signal_workflow('contract_active')

    @api.multi
    def force_activation(self):
        """ Used to transition sponsorships in active state. """
        self.signal_workflow('contract_validated')
        self.signal_workflow('contract_active')
        logger.info("Contracts " + str(self.ids) + " activated.")
        return True

    ##########################################################################
    #                             VIEW CALLBACKS                             #
    ##########################################################################
    @api.onchange('partner_id')
    def on_change_partner_id(self):
        """ On partner change, we set the new pol_number
        (for gift identification). """
        super(RecurringContract, self).on_change_partner_id()
        num_contracts = self.search_count(
            [('partner_id', '=', self.partner_id.id)])

        self.commitment_number = num_contracts

    @api.onchange('parent_id')
    def on_change_parent_id(self):
        """ If a previous sponsorship is selected, the origin should be
        SUB Sponsorship. """
        if self.parent_id:
            origin = self.env['recurring.contract.origin'].search(
                [('type', '=', 'sub')])[0]
            self.origin_id = origin.id

    @api.multi
    def open_contract(self):
        """ Used to bypass opening a contract in popup mode from
        res_partner view. """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Contract',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': self._name,
            'res_id': self.id,
            'target': 'current',
            'context': self.with_context(default_type=self.type).env.context
        }

    ##########################################################################
    #                            WORKFLOW METHODS                            #
    ##########################################################################
    @api.multi
    def contract_active(self):
        self.filtered(lambda c: not c.is_active).write({
            'activation_date': fields.Date.today()
        })
        self.write({'state': 'active'})

        # Write payment term in partner property
        for contract in self:
            contract.partner_id.customer_payment_mode_id = \
                contract.payment_mode_id
        return True

    @api.multi
    def contract_cancelled(self):
        self.write({
            'state': 'cancelled',
            'end_date': fields.Datetime.now()
        })
        self.clean_invoices()
        return True

    @api.multi
    def contract_terminated(self):
        self.write({
            'state': 'terminated',
            'end_date': fields.Datetime.now()
        })
        self.clean_invoices()
        return True

    @api.multi
    def contract_waiting(self):
        return self.write({'state': 'waiting'})

    @api.multi
    def action_cancel_draft(self):
        """ Set back a cancelled contract to draft state. """
        update_sql = "UPDATE RecurringContract SET state='draft', "\
            "end_date=NULL, activation_date=NULL, start_date=CURRENT_DATE"
        for contract in self.filtered(lambda c: c.state == 'cancelled'):
            query = update_sql
            if contract.child_id and not contract.child_id.is_available:
                query += ', child_id = NULL'
            self.env.cr.execute(query + " WHERE id = %s", [contract.id])
            contract.delete_workflow()
            contract.create_workflow()
            self.env.invalidate_all()
        return True

    ##########################################################################
    #                             PRIVATE METHODS                            #
    ##########################################################################
    @api.multi
    def _on_change_next_invoice_date(self, new_invoice_date):
        """ Disable rewind check on draft and mandate contracts. """
        contracts = self.filtered(
            lambda c: c.state not in ('draft', 'mandate'))
        super(RecurringContract, contracts)._on_change_next_invoice_date(
            new_invoice_date)

    def _get_filtered_invoice_lines(self, invoice_lines):
        return invoice_lines.filtered(lambda l: l.contract_id.id in self.ids)

    def _cancel_old_invoices(self, partner_id, date_invoice):
        """
            Cancel the open invoices of a contract
            which are older than a given date.
            If the invoice has only one contract -> cancel
            Else -> draft to modify the invoice and validate
        """
        invoice_line_obj = self.env['account.invoice.line']
        invoice_lines = invoice_line_obj.search([
            ('contract_id', 'in', self.ids),
            ('state', '=', 'open'),
            ('due_date', '<', date_invoice)])

        invoices = invoice_lines.mapped('invoice_id')

        for invoice in invoices:
            invoice_lines = invoice.invoice_line_ids

            inv_lines = self._get_filtered_invoice_lines(invoice_lines)

            if len(inv_lines) == len(invoice_lines):
                invoice.signal_workflow('invoice_cancel')
            else:
                invoice.action_cancel_draft()
                inv_lines.unlink()
                invoice.signal_workflow('invoice_open')

    def _clean_error(self):
        raise UserError(
            _('The sponsor has already paid in advance for this '
              'sponsorship, but the system was unable to automatically '
              'cancel the invoices. Please refer to an accountant for '
              'changing the attribution of his payment before cancelling '
              'the sponsorship.'))

    def _reset_open_invoices(self):
        """ Launch the task in asynchrnous job by default. """
        if self.env.context.get('async_mode', True):
            session = ConnectorSession.from_env(self.env)
            reset_open_invoices_job.delay(
                session, self._name, self.ids)
        else:
            self._reset_open_invoices_job()
        return True

    def _reset_open_invoices_job(self):
        """Clean the open invoices in order to generate new invoices.
        This can be useful if contract was updated when active."""
        invoices_canceled = self._clean_invoices()
        if invoices_canceled:
            invoice_obj = self.env['account.invoice']
            inv_update_ids = set()
            for contract in self:
                # If some invoices are left cancelled, we update them
                # with new contract information and validate them
                cancel_invoices = invoice_obj.search([
                    ('state', '=', 'cancel'),
                    ('id', 'in', invoices_canceled.ids)])
                if cancel_invoices:
                    inv_update_ids.update(cancel_invoices.ids)
                    cancel_invoices.action_cancel_draft()
                    contract._update_invoice_lines(cancel_invoices)
                # If no invoices are left in cancel state, we rewind
                # the next_invoice_date for the contract to generate again
                else:
                    contract.rewind_next_invoice_date()
                    invoicer = contract.group_id._generate_invoices()
                    if not invoicer.invoice_ids:
                        invoicer.unlink()
            # Validate again modified invoices
            validate_invoices = invoice_obj.browse(list(inv_update_ids))
            validate_invoices.signal_workflow('invoice_open')
        return True

    def _on_group_id_changed(self):
        """Remove lines of open invoices and generate them again
        """
        self._reset_open_invoices_job()
        for contract in self:
            # Update next_invoice_date of group if necessary
            if contract.group_id.next_invoice_date:
                next_invoice_date = fields.Datetime.from_string(
                    contract.next_invoice_date)
                group_date = fields.Datetime.from_string(
                    contract.group_id.next_invoice_date)
                if group_date > next_invoice_date:
                    contract.group_id._set_next_invoice_date()


##############################################################################
#                            CONNECTOR METHODS                               #
##############################################################################
def related_action_contracts(session, job):
    contract_ids = job.args[2]
    action = {
        'name': _("Contracts"),
        'type': 'ir.actions.act_window',
        'res_model': 'recurring.contract',
        'view_type': 'form',
        'view_mode': 'tree,form',
        'domain': [('id', 'in', contract_ids)],
    }
    return action


@job(default_channel='root.recurring_invoicer')
@related_action(action=related_action_contracts)
def reset_open_invoices_job(session, model_name, contract_ids):
    """Job for generating again open invoices of contracts."""
    contracts = session.env[model_name].browse(contract_ids)
    contracts._reset_open_invoices_job()
