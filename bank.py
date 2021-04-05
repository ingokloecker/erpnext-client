import utils
from datetime import datetime
from api import Api, LIMIT
from api_wrapper import gui_api_wrapper
import settings
import company
import easygui
from numpy import sign
from collections import defaultdict

class BankAccount:
    baccounts_by_iban = {}
    baccounts_by_name = {}
    baccounts_by_company = defaultdict(list)
    def __init__(self,doc):
        self.doc = doc
        self.company = company.Company.get_company(doc['company'])
        self.iban = doc['iban']
        self.name = doc['name']
        self.e_account = doc['account']
        BankAccount.baccounts_by_iban[self.iban] = self
        BankAccount.baccounts_by_name[self.name] = self
        BankAccount.baccounts_by_company[self.company.name].append(self)
    def blz(self):
        return self.iban[4:12]
    @classmethod
    def init_baccounts(cls):
        for bacc in gui_api_wrapper(Api.api.get_list,'Bank Account'):
            BankAccount(bacc)

class BankTransaction:
    def __init__(self,doc):
        self.doc = doc
        self.date = doc['date']
        self.debit = doc['debit']
        self.credit = doc['credit']
        self.amount = -self.debit if self.debit else self.credit
        self.bank_account = doc['bank_account']
        self.baccount = BankAccount.baccounts_by_name[self.bank_account]
        self.company_name = doc['company']
        self.company = company.Company.companies_by_name[self.company_name]
        if 'description' in doc:
            self.description = doc['description']
        else:    
            self.description = ""

    def show(self):
        return(self.doc['name']+" {}\n{}\n{:.2f}€".format(utils.show_date4(self.date),self.description,self.amount))
    def journal_entry(self,cacc_name):
        accounts = [{'account': self.baccount.e_account,
                     'cost_center': self.company.cost_center,
                     'debit': self.credit,
                     'debit_in_account_currency': self.credit,
                     'credit': self.debit,
                     'credit_in_account_currency': self.debit },
                    {'account': cacc_name,
                     'cost_center': self.company.cost_center,
                     'debit': self.debit,
                     'debit_in_account_currency': self.debit,
                     'credit': self.credit,
                     'credit_in_account_currency': self.credit}]
        entry = {'doctype' : 'Journal Entry',
                 'title': self.description[0:140],
                 'voucher_type': 'Journal Entry',
                 'company': self.company_name,
                 'posting_date': self.date,
                 'user_remark': self.description,
                 'accounts':accounts}
        #print(entry)
        j = gui_api_wrapper(Api.api.insert,entry)
        #print(j)
        print("Buchungssatz {} erstellt".format(j['name']))
        if j:
            self.doc['status'] = 'Reconciled'
            self.doc['payment_entries'] = \
                 [{'payment_document': 'Journal Entry',
                   'payment_entry': j['name'],
                   'allocated_amount': abs(self.amount)}]
            gui_api_wrapper(Api.api.update,self.doc)

    def payment(self,inv):
        references =  \
            [{'reference_doctype' : 'Sales Invoice' if inv.is_sales else 'Purchase Invoice',
              'reference_name' : inv.name,
              'allocated_amount' : abs(self.amount)}]
        entry = {'doctype' : 'Payment Entry',
                 'title' : inv.party+" "+inv.reference,
                 'payment_type': 'Receive' if inv.is_sales else 'Pay',
                 'posting_date': self.date,
                 'reference_no': inv.reference,
                 'reference_date': self.date,
                 'party' : inv.party,
                 'party_type' : inv.party_type,
                 'company': self.company_name,
                 'paid_from' : settings.DEBIT_TO_ACCOUNT if inv.is_sales else self.baccount.e_account,
                 'paid_to': self.baccount.e_account if inv.is_sales else settings.CREDIT_TO_ACCOUNT,
                 'paid_amount' : abs(self.amount),
                 'received_amount' : abs(self.amount),
                 'source_exchange_rate': 1.0,
                 'target_exchange_rate': 1.0,
                 'exchange_rate': 1.0,
                 'references' : references}
        p = gui_api_wrapper(Api.api.insert,entry)
        if p:
            self.doc['status'] = 'Reconciled'
            self.doc['payment_entries'] = \
                 [{'payment_document': 'Payment Entry',
                   'payment_entry': p['name'],
                   'allocated_amount': abs(self.amount)}]
            gui_api_wrapper(Api.api.update,self.doc)
                 
    def find_cacc(self,sinvs,pinvs):
        if self.credit:
            accounts = self.company.leaf_accounts_for_debit
            invs = sinvs
        else:    
            accounts = self.company.leaf_accounts_for_credit
            invs = pinvs
        invs.sort(key=lambda inv: abs(inv.outstanding-abs(self.amount)))
        inv_texts = list(map(lambda inv: utils.showlist([inv.name,inv.party,inv.reference,inv.outstanding]),invs))
        account_names = list(map(lambda acc: acc['name'],accounts))
        title = "Rechnung oder Buchungskonto wählen"
        msg = "Bankbuchung:\n"+self.show()+"\n\n"+title+"\n"
        choice = easygui.choicebox(msg, title, inv_texts+account_names)
        if choice in inv_texts:
            inv = invs[inv_texts.index(choice)]
            return (inv,None)
        return (None,choice)
    
    def transfer(self,sinvs,pinvs):
        (inv,cacc) = self.find_cacc(sinvs,pinvs)
        if inv:
            self.payment(inv)
        if cacc:
            self.journal_entry(cacc)
            
class BankStatementEntry:
    def __init__(self,bank_statement):
        self.bank_statement = bank_statement

    def show(self):
        return("{}\n{}\n{}\n{:.2f}€".format(utils.show_date4(self.posting_date),self.purpose,
                                            self.partner,self.amount))
    def cleanup(self):
        self.purpose = utils.remove_space(self.purpose)
        self.partner = utils.remove_space(self.partner)

    def bank_transation(self):
        entry = {'doctype' : 'Bank Transaction',
                 'date' : self.posting_date,
                 'bank_account' : self.bank_statement.baccount.name,
                 'description' : self.purpose+" "+self.partner,
                 'currency' : 'EUR',
                 'debit' : -self.amount if self.amount < 0 else 0,
                 'credit' : self.amount if self.amount > 0 else 0 }
        return entry

class BankStatement:
    def __init__(self,bacc):
        self.baccount = bacc
        self.entries = []
        self.read_iban = None
        self.sbal = None
        self.ebal = None
        
    def read_sparkasse(self,infile):
        first_row = True
        for row in utils.get_csv('iso-8859-4',infile):
            if not row:
                continue
            if first_row:
                first_row = False
                continue
            be = BankStatementEntry(self)
            self.iban = row[0]
            be.posting_date = utils.convert_date2(row[1])
            be.purpose = row[4]
            be.partner = row[11]
            be.partner_iban = row[12]
            be.amount = utils.read_float(row[14])
            be.cleanup()
            self.entries.append(be)

    def read_sparda_ethik(self,infile,is_sparda=True):
        blz = None
        baccount_no = None
        r = 0 if is_sparda else 1
        for row in utils.get_csv('iso-8859-4',infile,replacenl=is_sparda):
            if not row:
                continue
            if row[0]=='BLZ:':
                blz = int(row[1])
                continue
            if row[0]=='Konto:':
                baccount_no = int(row[1])
                continue
            date = utils.convert_date4(row[0])
            if not date:
                continue
            if row[9+r]=='Anfangssaldo':
                self.sbal = utils.read_float(row[11+r],row[12+r])
                continue
            if row[9+r]=='Endsaldo':
                self.ebal = utils.read_float(row[11+r],row[12+r])
                continue
            be = BankStatementEntry(self)
            be.posting_date = date
            be.purpose = row[8+r]
            be.partner = row[3+r]
            be.partner_iban = row[5+r]
            be.amount = utils.read_float(row[11+r],row[12+r])
            be.cleanup()
            self.entries.append(be)
        if blz and baccount_no:
            self.iban = utils.iban_de(blz,baccount_no)

    @classmethod
    def get_baccount(cls,infile):
        blz = None
        baccount_no = None
        iban = None
        for row in utils.get_csv('iso-8859-4',infile):
            if not row:
                continue
            if row[0]=='BLZ:':
                blz = int(row[1])
                continue
            if row[0]=='Konto:':
                baccount_no = int(row[1])
                continue
            if row[0][0:2]=='DE':
                iban = row[0]
                break
            if blz and baccount_no:
                iban = utils.iban_de(blz,baccount_no)
                break
        if iban and iban in BankAccount.baccounts_by_iban:
            return (BankAccount.baccounts_by_iban[iban],iban)
        else:
            return (None,iban)

    @classmethod
    def read_statement(cls,infile):
        (bacc,iban) = BankStatement.get_baccount(infile)
        if not bacc:
            easygui.msgbox("Konto unbekannt: IBAN {}".format(iban))
            exit(1)            
        b = BankStatement(bacc)
        if bacc.blz()=='83094495':
            b.read_sparda_ethik(infile,is_sparda=False)
        elif bacc.blz()=='25090500':
            b.read_sparda_ethik(infile,is_sparda=True)
        elif bacc.blz()=='29050101':
            b.read_sparkasse(infile)
        else:
            easygui.msgbox("Keine Importmöglichkeit für BLZ {}".format(bacc.blz()))
            exit(1)
        return b

    @classmethod
    def process_file(cls,infile):
        b = BankStatement.read_statement(infile)
        b.transactions = []
        for be in b.entries:
            bt = be.bank_transation()
            bt1 = bt.copy()
            del bt1['doctype']
            bt1['status'] = ['!=','Cancelled']
            #todo: relax the filter wrt the date (which sometimes is adapted by the bank)
            bts = gui_api_wrapper(Api.api.get_list,'Bank Transaction',filters=bt1)
            if not bts:
                gui_api_wrapper(Api.api.insert,bt)
                b.transactions.append(bt)
        doc = b.baccount.doc
        doc['last_integration_date'] = datetime.today().strftime('%Y-%m-%d')
        b.baccount.doc = gui_api_wrapper(Api.api.update_with_doctype,doc,"Bank Account")
        return b